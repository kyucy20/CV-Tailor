"""Review what the model produced, edit it, and approve or discard.

Every button here either writes to the database or reads a file. The two
expensive operations — calling the model and rendering PDFs — happen only
inside a button branch, so a rerun caused by ticking a checkbox never
repeats them.

Re-render and Regenerate both write a NEW application row. Nothing is ever
overwritten: the previous version keeps its row and its PDFs, so a bad edit
costs one click to walk back.
"""

from __future__ import annotations

import streamlit as st

from db import (
    delete_job, get_job, get_jobs, latest_application, save_application,
    set_job_meta, set_status,
)
from generate import MODEL, generate
from renderer import letter_header, render, render_cover_letter
from schema import MIN_PROJECTS, Selection
from ui import (
    OUT, bootstrap, bullet_text, flash, job_label, profile, read_pdf,
    safe_filename, show_flashes,
)

st.set_page_config(page_title="Queue", page_icon="📝", layout="wide")
bootstrap()

st.title("📝 Queue")

show_flashes()


def next_paths(job_id: int) -> tuple:
    """Fresh, unused paths for one more render of this job.

    batch.py wrote cv.pdf / cover_letter.pdf; every revision made here lands
    beside them as _v2, _v3 and so on, so the application row that pointed at
    the old file still points at a file that exists.
    """
    d = OUT / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    n = 2
    while (d / f"cv_v{n}.pdf").exists():
        n += 1
    return d / f"cv_v{n}.pdf", d / f"cover_letter_v{n}.pdf"


def store(job_id: int, sel: Selection) -> int:
    """Render one Selection and record it as a new application row."""
    cv_path, letter_path = next_paths(job_id)
    render(profile(), sel.summary_id, sel.bullet_ids, sel.lang, sel.angle, str(cv_path))
    recipient, subject = letter_header(sel)
    render_cover_letter(
        profile(), sel.cover_letter, sel.lang, str(letter_path),
        recipient=recipient, subject=subject,
    )
    set_job_meta(job_id, sel.company, sel.role_title, sel.job_location)
    return save_application(job_id, sel, str(cv_path), str(letter_path), model=MODEL)


jobs = get_jobs("generated")
if not jobs:
    st.info("Nothing to review. Generate something on the Intake page first.")
    st.stop()

job = st.selectbox(
    f"{len(jobs)} awaiting review",
    jobs,
    format_func=job_label,
)
app = latest_application(job["id"])
if app is None:
    st.error(
        f"Job #{job['id']} is marked 'generated' but has no application row. "
        f"Re-run generation for it from the Intake page."
    )
    st.stop()

# ---------------------------------------------------------------------------
# What the model decided
# ---------------------------------------------------------------------------

left, right = st.columns([2, 1])
with left:
    st.subheader(job["company"] or "Company not stated")
    st.write(f"**{job['role_title'] or 'Role not stated'}**")
    st.caption(job["job_location"] or "Location not stated")
with right:
    st.metric("Language / angle", f"{app['lang']} · {app['angle']}")
    st.caption(f"application #{app['id']} · {app['created_at'][:16].replace('T', ' ')}")
    if job["url"]:
        st.caption(f"[posting]({job['url']})")

if app["unmatched_requirements"]:
    with st.expander(f"⚠️ {len(app['unmatched_requirements'])} unmatched requirement(s)", expanded=True):
        for req in app["unmatched_requirements"]:
            st.write(f"• {req}")
else:
    st.caption("✅ no unmatched requirements")

st.divider()

# ---------------------------------------------------------------------------
# Edit: which bullets, and the letter text
# ---------------------------------------------------------------------------

prof = profile()
bullets, letter_col = st.columns([1, 1])

with bullets:
    st.subheader("Bullets")
    st.caption("Untick to drop a bullet, then Re-render.")
    kept = []
    for bid in app["bullet_ids"]:
        # Key includes the application id so switching jobs — or generating a
        # new version — starts from that version's selection, not the last
        # one you happened to look at.
        if st.checkbox(
            f"**{bid}** — {bullet_text(prof, bid, app['lang'], app['angle'])}",
            value=True,
            key=f"bullet_{app['id']}_{bid}",
        ):
            kept.append(bid)

    projects_kept = len({prof.parent_of(b).id for b in kept if b in prof.project_of_bullet()})
    if not kept:
        st.error("No bullets selected — the CV would render empty.")
    elif projects_kept < MIN_PROJECTS:
        st.warning(
            f"{projects_kept} project(s) would show; generation requires "
            f"{MIN_PROJECTS}. Re-render will still do it — this is your call, "
            f"not the model's."
        )

with letter_col:
    st.subheader("Cover letter")
    edited_letter = st.text_area(
        "Edited text is what gets rendered and saved.",
        value=app["cover_letter"],
        height=420,
        key=f"letter_{app['id']}",
    )
    changed = edited_letter.strip() != (app["cover_letter"] or "").strip()
    st.caption("✏️ edited — Re-render to keep it" if changed else "unchanged from generation")

st.divider()

# ---------------------------------------------------------------------------
# Actions. Model calls and renders live here and nowhere else.
# ---------------------------------------------------------------------------

c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    if st.button("🔄 Re-render", type="primary", disabled=not kept, width="stretch"):
        # Validated without a context, which deliberately skips the id and
        # MIN_PROJECTS checks: the ids came out of a stored row, and dropping
        # below three projects is a decision being made here on purpose.
        sel = Selection.model_validate({
            "summary_id": app["summary_id"],
            "bullet_ids": kept,
            "lang": app["lang"],
            "angle": app["angle"],
            "cover_letter": edited_letter,
            "unmatched_requirements": app["unmatched_requirements"],
            "company": job["company"] or "",
            "job_location": job["job_location"] or "",
            "role_title": job["role_title"] or "",
        })
        with st.spinner("Rendering…"):
            new_id = store(job["id"], sel)
        flash("success", f"Saved as application #{new_id}.")
        st.rerun()

with c2:
    if st.button("♻️ Regenerate", width="stretch"):
        with st.spinner("Asking the model again…"):
            try:
                sel = generate(prof, job["description"])
                new_id = store(job["id"], sel)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
            else:
                flash("success", f"Regenerated as application #{new_id}.")
                st.rerun()

with c3:
    if st.button("✅ Approve", width="stretch"):
        set_status(job["id"], "approved")
        st.rerun()

with c4:
    if st.button("🗑️ Discard", width="stretch", help="Keep the record, drop it from review"):
        set_status(job["id"], "discarded")
        st.rerun()

with c5:
    # Discard keeps the row so the Tracker still shows what was passed over.
    # This is for a job that should never have been added — a bad paste, a
    # truncated fetch — where the record itself is the problem.
    if st.session_state.get("confirm_delete") == job["id"]:
        st.caption("Delete for good?")
        yes, no = st.columns(2)
        if yes.button("✔", key="del_yes", width="stretch"):
            removed = delete_job(job["id"])
            st.session_state.pop("confirm_delete", None)
            flash(
                "info",
                f"Deleted job #{job['id']} and {removed} application(s). "
                f"The PDFs are still under out/{job['id']}/.",
            )
            st.rerun()
        if no.button("✖", key="del_no", width="stretch"):
            st.session_state.pop("confirm_delete", None)
            st.rerun()
    elif st.button("❌ Delete", width="stretch", help="Remove the job and its applications"):
        st.session_state["confirm_delete"] = job["id"]
        st.rerun()

# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

st.divider()
d1, d2 = st.columns(2)

# What the recruiter sees in their downloads folder. Taken from the CV rather
# than hardcoded, so it follows the name in CV.json. The files on disk keep
# their out/<job_id>/cv_v2.pdf names — those have to stay distinct per job.
owner = prof.meta.text(app["lang"])["name"]

# Company appended so a downloads folder full of applications stays sortable
# by name and still says who each one is for. Omitted entirely when the
# company is unknown, rather than leaving a dangling separator.
company = safe_filename(job["company"] or "")
suffix = f" - {company}" if company else ""

for col, path, label, name in (
    (d1, app["cv_path"], "⬇️ CV (PDF)", f"{owner}'s CV{suffix}.pdf"),
    (d2, app["letter_path"], "⬇️ Cover letter (PDF)", f"{owner}'s Cover Letter{suffix}.pdf"),
):
    with col:
        data = read_pdf(path)
        if data is None:
            st.caption(f"missing on disk: {path or '—'}")
        else:
            st.download_button(label, data, file_name=name, mime="application/pdf",
                               width="stretch")
