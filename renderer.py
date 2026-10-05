"""Render a CV.json profile to PDF via Typst.

Nothing here generates text. Every sentence on the page is copied verbatim
from the profile; the renderer only *selects* (which summary, which bullets,
which language and angle) and hands the result to template.typ for layout.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from schema import ANGLES, LANGS, Experience, Profile, Project, Selection, localized

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.typ"
LETTER_TEMPLATE = HERE / "cover_letter.typ"

# The one word of chrome on the subject line; the role title itself is copied
# from the posting by the model.
SUBJECT_WORD = {"EN": "Application", "DE": "Bewerbung"}

BREW_HINT = (
    "typst is not installed. Install it with:\n\n    brew install typst\n\n"
    "(then re-run; no other layout backend is used)"
)


def _typst_binary() -> str:
    exe = os.environ.get("TYPST_BIN") or shutil.which("typst")
    if not exe:
        raise RuntimeError(BREW_HINT)
    return exe


def build_payload(
    profile: Profile,
    summary_id: str,
    bullet_ids: list[str],
    lang: str,
    angle: str,
) -> dict:
    """Resolve the selection into the JSON payload template.typ consumes.

    Raises KeyError for unknown ids and LookupError when a bullet has no
    variant in the requested language.
    """
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {LANGS}, got {lang!r}")
    if angle not in ANGLES:
        raise ValueError(f"angle must be one of {ANGLES}, got {angle!r}")

    summary = profile.find_summary(summary_id)
    if summary.lang != lang:
        raise LookupError(
            f"summary {summary_id!r} is {summary.lang!r} but the render is {lang!r}; "
            f"refusing to mix languages. Pick a {lang!r} summary."
        )

    # Group bullets under their parent, keeping the order the ids were given:
    # parents appear in order of first mention, bullets in the given order.
    order: list[str] = []
    grouped: dict[str, list[str]] = {}
    parents: dict[str, Experience | Project] = {}

    for bid in bullet_ids:
        bullet = profile.find_bullet(bid)          # KeyError names the id
        parent = profile.parent_of(bid)
        if parent.id not in grouped:
            order.append(parent.id)
            grouped[parent.id] = []
            parents[parent.id] = parent
        grouped[parent.id].append(bullet.variant(lang, angle).text)

    experience, projects = [], []
    for pid in order:
        parent = parents[pid]
        texts = grouped[pid]
        if isinstance(parent, Experience):
            experience.append(
                {
                    "company": parent.company,
                    "role": localized(parent.role, lang, f"{parent.id}.role"),
                    "location": localized(parent.location, lang, f"{parent.id}.location"),
                    "start": parent.start,
                    "end": parent.end,
                    "bullets": texts,
                }
            )
        else:
            projects.append(
                {
                    "name": localized(parent.name, lang, f"{parent.id}.name"),
                    "year": parent.year,
                    "stack": parent.stack,
                    "bullets": texts,
                }
            )

    return {
        "lang": lang,
        "angle": angle,
        "meta": profile.meta.text(lang),
        "summary": summary.text,
        "experience": experience,
        "projects": projects,
        # Education and languages always print in full — no selection.
        # Localised education fields are resolved into `lang` here so the
        # template only ever sees plain strings.
        "education": [
            {"start": e.start, "end": e.end, **e.text(lang)} for e in profile.education
        ],
        "languages": [l.text(lang) for l in profile.languages],
        "skills": [
            {"category": cat, "names": [s.name for s in group]}
            for cat, group in profile.skills_by_category().items()
        ],
    }


def render(
    profile: Profile,
    summary_id: str,
    bullet_ids: list[str],
    lang: str,
    angle: str,
    out_path: str,
) -> None:
    """Render the selected content to a PDF at out_path."""
    payload = build_payload(profile, summary_id, bullet_ids, lang, angle)
    preview = HERE / "preview_data.json"
    preview.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = _compile(TEMPLATE, payload, out_path)
    _warn_if_multipage(out)


def _compile(template: Path, payload: dict, out_path: str) -> Path:
    """Run one Typst compile of `template` over `payload`, returning the PDF
    path. Shared by the CV and the cover letter so both get the same root,
    font path and error reporting."""
    if not template.exists():
        raise FileNotFoundError(f"template not found: {template}")
    typst = _typst_binary()

    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # The data file must sit next to the template so the template's relative
    # json() call resolves, and so a template that imports siblings still works.
    fd, tmp = tempfile.mkstemp(prefix=".cvdata_", suffix=".json", dir=HERE)
    os.close(fd)
    data_file = Path(tmp)
    try:
        data_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd = [
            typst, "compile",
            "--root", str(HERE),
            "--input", f"data={data_file.name}",
        ]
        # Project-local fonts (e.g. Font Awesome for the contact icons) so the
        # template works without a system-wide font install.
        fonts = HERE / "fonts"
        if fonts.is_dir():
            cmd += ["--font-path", str(fonts)]
        cmd += [str(template), str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"typst compile failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
            )
    finally:
        data_file.unlink(missing_ok=True)
    return out


def letter_header(sel: Selection) -> tuple[str | None, str | None]:
    """The recipient and subject lines for a Selection, formatted as in the
    reference letter: "SAP SE Walldorf, Germany" and "Application: SAP iXp
    Intern (f/m/d)". Either comes back None when the model left it blank, and
    the template then omits that line rather than printing a stray colon.
    """
    parts = [p for p in (sel.company.strip(), sel.job_location.strip()) if p]
    recipient = " ".join(parts) or None

    role = sel.role_title.strip()
    subject = f"{SUBJECT_WORD[sel.lang]}: {role}" if role else None
    return recipient, subject


def render_cover_letter(
    profile: Profile,
    letter: str,
    lang: str,
    out_path: str,
    recipient: str | None = None,
    subject: str | None = None,
) -> None:
    """Render the model's cover letter to a PDF at out_path.

    The letter is printed verbatim — split into paragraphs on blank lines and
    nothing more. No salutation or sign-off is added here: whatever the model
    wrote is the whole letter, exactly as it appears on stdout.

    `recipient` ("SAP SE Walldorf, Germany") and `subject` ("Application:
    ...") fill the two 11pt lines the reference letter carries under the name.
    Both are omitted from the page when left as None.
    """
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {LANGS}, got {lang!r}")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", letter.strip()) if p.strip()]
    if not paragraphs:
        raise ValueError("cover letter is empty — nothing to render")

    payload = {
        "lang": lang,
        "meta": profile.meta.text(lang),
        "body": paragraphs,
        "recipient": recipient,
        "subject": subject,
    }
    preview = HERE / "preview_letter.json"
    preview.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _compile(LETTER_TEMPLATE, payload, out_path)


def _warn_if_multipage(pdf: Path) -> None:
    """Soft check — the layout targets one page for a 5-6 bullet selection."""
    try:
        import pypdf
    except ImportError:
        return
    try:
        pages = len(pypdf.PdfReader(str(pdf)).pages)
    except Exception:
        return
    if pages > 1:
        print(f"  warning: {pdf.name} is {pages} pages — trim the bullet selection")


def load_profile(path: str | Path = HERE / "CV.json") -> Profile:
    return Profile.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
