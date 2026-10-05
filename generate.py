"""Ask the model which existing CV pieces fit a job posting.

The model returns bullet ids, never CV text. The one thing it writes is the
cover letter. Everything else is a lookup into CV.json, so a hallucinated
sentence cannot reach the page — only a hallucinated *id* is possible, and
Selection rejects those by name.

Every provider-specific line lives in call_model(). Swapping Anthropic for
another backend means rewriting that one function; prompt building, parsing,
validation and rendering never learn which model answered.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from schema import MAX_BULLETS, SKILL_CATEGORIES, Profile, Selection

HERE = Path(__file__).resolve().parent
PROMPT_FILE = HERE / "prompt.txt"
ENV_FILE = HERE / ".env"

# Verified against platform.claude.com/docs/en/about-claude/models/overview on
# 2026-09-17: claude-opus-5 is the current default recommendation. The ID is a
# pinned snapshot, not a moving alias. claude-sonnet-5 is the same API at a
# fifth of the price if this task turns out not to need Opus.
MODEL = "claude-opus-5"
MAX_TOKENS = 16000

# prompt.txt already asks for a bare JSON object; this repeats it at the system
# level, where it carries more weight, without touching the prompt file.
SYSTEM = (
    "You return a single raw JSON object and nothing else. No markdown code "
    "fences, no ```json, no commentary before or after the JSON."
)

# Read .env from the project directory rather than the working directory, so
# the key resolves the same whether PyCharm or a shell started the process.
# Real environment variables still win over the file.
load_dotenv(ENV_FILE)

# Which stored variant to show the model for relevance judging. The renderer
# substitutes the real one once lang/angle are known, so this is only a probe.
BANK_LANG, BANK_ANGLE = "EN", "technical"

NO_CREDENTIALS = f"""ANTHROPIC_API_KEY is not set — the request never left this machine.

Put the key in {ENV_FILE.name}, in the project root:

    ANTHROPIC_API_KEY=sk-ant-...

That file is read on startup and is already in .gitignore. An exported
ANTHROPIC_API_KEY in the environment works too and takes precedence.

Keys: https://console.anthropic.com/settings/keys"""

NO_WORKSPACE = f"""This API key is not scoped to a workspace, so the API needs to be told
which workspace to bill. Two ways to fix it, either is fine:

  1. Create a workspace-scoped key and use that instead:
     https://console.anthropic.com/settings/keys

  2. Add the workspace id next to the key in {ENV_FILE.name}:

         ANTHROPIC_WORKSPACE_ID=wrkspc_...

     Workspaces: https://console.anthropic.com/settings/workspaces"""


def _representative(bullet) -> str:
    """One variant's text, for judging relevance. Prefers EN/technical but
    falls back to whatever the bullet actually has."""
    try:
        return bullet.variant(BANK_LANG, BANK_ANGLE).text
    except LookupError:
        return bullet.variants[0].text


def build_bullet_bank(profile: Profile) -> str:
    lines = []
    for group, label in ((profile.experience, "experience"), (profile.projects, "project")):
        for item in group:
            title = item.company if hasattr(item, "company") else item.name.get("en", item.id)
            lines.append(f"\n[{label}: {title}]")
            for b in item.bullets:
                tags = ", ".join(b.tags)
                lines.append(f"{b.id} | {tags} | {_representative(b)}")
    return "\n".join(lines).strip()


def build_summary_options(profile: Profile) -> str:
    return "\n".join(
        f"{s.id} | lang={s.lang} | tags: {', '.join(s.tags)} | {s.text}"
        for s in profile.summaries
    )


def build_education(profile: Profile) -> str:
    """Degree, institution and the detail line, in both stored languages.

    The model writes the cover letter, and a posting that asks about a
    Vertiefung or a specialisation is asking about something that lives here
    rather than in the bullet bank. Taken from CV.json so the electives are
    stated in one place and cannot drift from what the CV itself prints.
    """
    lines = []
    for entry in profile.education:
        for lang in ("EN", "DE"):
            text = entry.text(lang)
            detail = text.get("detail") or ""
            lines.append(
                f"[{lang}] {text['credential']}, {text['institution']}"
                + (f" — {detail}" if detail else "")
            )
    return "\n".join(lines)


def build_skills(profile: Profile) -> str:
    """The skills and languages that print on every CV, whatever is selected.

    Without this the model only ever sees the bullet bank, so it reports
    things that ARE on the page as unmatched — "MS-Office-Kenntnisse nicht im
    Lebenslauf belegt" when Office is in the skills line, or German and
    English missing when both are printed under Languages. Those false gaps
    are worse than silence: they talk the candidate out of a requirement that
    is actually met.
    """
    lines = []
    for category in SKILL_CATEGORIES:
        names = [s.name for s in profile.skills if s.category == category]
        if names:
            lines.append(f"{category}: " + ", ".join(names))
    langs = ", ".join(
        f"{l.text('EN')['name']} ({l.level})" for l in profile.languages
    )
    lines.append(f"languages spoken: {langs}")
    return "\n".join(lines)


def selection_schema() -> dict:
    """Selection's JSON schema, as shown to the model in the prompt."""
    schema = Selection.model_json_schema()
    schema["additionalProperties"] = False
    schema["properties"]["bullet_ids"]["maxItems"] = MAX_BULLETS
    return schema


def build_prompt(profile: Profile, job_text: str) -> str:
    template = PROMPT_FILE.read_text(encoding="utf-8")
    # Plain replacement, not str.format — the schema is full of braces.
    return (
        template
        .replace("{{JOB_TEXT}}", job_text.strip())
        .replace("{{BULLET_BANK}}", build_bullet_bank(profile))
        .replace("{{SUMMARY_OPTIONS}}", build_summary_options(profile))
        .replace("{{EDUCATION}}", build_education(profile))
        .replace("{{SKILLS}}", build_skills(profile))
        .replace("{{SCHEMA}}", json.dumps(selection_schema(), indent=2))
    )


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(NO_CREDENTIALS)
    # An org-wide key has to name the workspace to bill; a workspace-scoped
    # key already carries it. Optional, so only sent when it is set.
    workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    headers = {"anthropic-workspace-id": workspace} if workspace else None

    # Passing the key explicitly rather than letting the SDK search the
    # environment: the failure above is clearer than the SDK's bare TypeError.
    return anthropic.Anthropic(api_key=key, default_headers=headers)


# ```json { ... } ``` or ``` { ... } ```, with or without trailing newline.
_FENCE = re.compile(r"\A\s*```[a-zA-Z0-9_-]*\s*\n(?P<body>.*?)\n?\s*```\s*\Z", re.S)


def _strip_fences(text: str) -> str:
    """Unwrap a markdown code fence if the model added one anyway.

    Belt and braces: both prompt.txt and the system prompt ask for bare JSON,
    but a fenced reply is the single most likely way for that to be ignored,
    and it would otherwise fail as a JSONDecodeError on character 1.
    """
    match = _FENCE.match(text)
    return match.group("body").strip() if match else text.strip()


def call_model(prompt: str) -> str:
    """Send one prompt, return the raw response text.

    The only function in the program that knows which provider is in use.
    """
    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        # Account-shaped problems are actionable; everything else is a real
        # error and keeps its traceback.
        message = str(getattr(e, "message", "") or e)
        if "workspace" in message.lower():
            raise RuntimeError(NO_WORKSPACE) from None
        if isinstance(e, anthropic.AuthenticationError):
            raise RuntimeError(f"the API rejected the key: {message}") from None
        raise
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined this request")

    # Adaptive thinking is on by default for this model, so the reply can open
    # with thinking blocks; only the text blocks carry the answer.
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise RuntimeError(
            f"the model returned no text (stop_reason={response.stop_reason})"
        )
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"the reply hit the {MAX_TOKENS}-token ceiling and is truncated; "
            f"raise MAX_TOKENS in generate.py"
        )
    return _strip_fences(text)


def generate(profile: Profile, job_text: str) -> Selection:
    """Pick summary, bullets, lang and angle for this posting, and draft a
    cover letter. Retries once if the first answer fails validation."""
    if not job_text.strip():
        raise ValueError("job_text is empty — nothing to tailor against")

    context = {
        "valid_ids": profile.selectable_ids(),
        "project_of": profile.project_of_bullet(),
    }
    prompt = build_prompt(profile, job_text)

    raw = call_model(prompt)
    try:
        return Selection.model_validate(json.loads(raw), context=context)
    except (ValidationError, json.JSONDecodeError) as first_error:
        # Feed the failure back verbatim; the ids it names are the whole point.
        # One prompt rather than a chat turn, so call_model stays str -> str.
        retry_prompt = (
            f"{prompt}\n\n"
            f"--- your previous answer ---\n{raw}\n\n"
            f"--- that answer failed validation ---\n{first_error}\n\n"
            f"Return corrected JSON. Every id must be copied exactly from the "
            f"bullet bank or summary options above. Do not invent ids and do "
            f"not write any CV text."
        )
        try:
            return Selection.model_validate(json.loads(call_model(retry_prompt)), context=context)
        except (ValidationError, json.JSONDecodeError) as second_error:
            raise ValueError(
                f"selection failed validation twice.\n"
                f"first attempt:\n{first_error}\n\nretry:\n{second_error}"
            ) from second_error
