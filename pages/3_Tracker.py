"""Where every application stands, and an Excel export of the lot.

Read-only except for the status buttons and the export. No model call and no
PDF render happens on this page at all.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from db import STATUSES, delete_job, get_jobs, latest_application, set_status
from ui import (
    APPLY_ROUTES, OUT, apply_job_edits, bootstrap, diff_job_rows, flash,
    job_label, show_flashes,
)

st.set_page_config(page_title="Tracker", page_icon="📊", layout="wide")
bootstrap()

st.title("📊 Tracker")

show_flashes()

EXPORT = OUT / "applications.xlsx"

# Where a job can go from the Tracker. Approving and discarding belong to the
# review page; this page is for what happens after something is sent.
ADVANCE_TO = ("submitted", "interview", "rejected")

jobs = get_jobs()
if not jobs:
    st.info("No jobs yet. Add one on the Intake page.")
    st.stop()


def rows(with_application: bool) -> list[dict]:
    """Flatten jobs, optionally joined with their latest application.

    latest_application is called per job rather than joined in SQL, because
    the UI layer does not write SQL — at this scale the extra queries cost
    nothing and the decoding of the JSON list columns stays in one place.
    """
    out = []
    for job in jobs:
        row = {
            "id": job["id"],
            "status": job["status"],
            "company": job["company"] or "",
            "role": job["role_title"] or "",
            "location": job["job_location"] or "",
            "apply_route": job["apply_type"] or "",
            # Rows predating the source column read NULL; "—" keeps the
            # spreadsheet from showing a blank that looks like a bug.
            "source": job["source"] or "—",
            "added": (job["created_at"] or "")[:10],
        }
        if with_application:
            app = latest_application(job["id"])
            row |= {
                "url": job["url"] or "",
                "lang": app["lang"] if app else "",
                "angle": app["angle"] if app else "",
                # Excel has no list type: join for a cell a human can read.
                "bullets": ", ".join(app["bullet_ids"]) if app else "",
                "unmatched": "; ".join(app["unmatched_requirements"]) if app else "",
                "model": app["model"] if app else "",
                "cv_path": app["cv_path"] if app else "",
                "letter_path": app["letter_path"] if app else "",
                "cover_letter": app["cover_letter"] if app else "",
                "generated_at": (app["created_at"][:16].replace("T", " ") if app else ""),
            }
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

counts = {s: sum(1 for j in jobs if j["status"] == s) for s in STATUSES}
cols = st.columns(len(STATUSES))
for col, status in zip(cols, STATUSES):
    col.metric(status, counts[status])

# Editable: the fields a person corrects. `id`, `source` and `added` are
# facts about how the row got here, not opinions, so they stay locked —
# editing an id would silently write to a different job.
READ_ONLY = ["id", "source", "added"]
EDITABLE = ["status", "company", "role", "location", "apply_route"]

original = pd.DataFrame(rows(with_application=False))
edited = st.data_editor(
    original,
    width="stretch",
    hide_index=True,
    disabled=READ_ONLY,
    num_rows="fixed",          # rows are added by Intake and removed below
    key="tracker_table",
    column_config={
        "status": st.column_config.SelectboxColumn(
            "status", options=list(STATUSES), required=True,
            help="Same set set_status accepts — anything else is rejected.",
        ),
        "apply_route": st.column_config.SelectboxColumn(
            "apply_route", options=list(APPLY_ROUTES), required=False,
        ),
        "company": st.column_config.TextColumn("company"),
        "role": st.column_config.TextColumn("role"),
        "location": st.column_config.TextColumn("location"),
    },
)

# Nothing is written while you type. Streamlit reruns on every keystroke, and
# a save per keystroke would be one database write per character.
changes = diff_job_rows(original.to_dict("records"), edited.to_dict("records"))

if changes:
    st.caption(
        "Unsaved: "
        + ", ".join(f"#{c['id']} ({', '.join(sorted(c['fields']))})" for c in changes)
    )
    if st.button(f"💾 Save {len(changes)} change(s)", type="primary"):
        try:
            saved = apply_job_edits(changes)
        except (ValueError, KeyError) as e:
            flash("error", f"Nothing saved: {e}")
        else:
            flash("success", f"Saved {saved} change(s).")
            # Drop the grid's edit buffer so it re-reads from the database.
            st.session_state.pop("tracker_table", None)
        st.rerun()

# ---------------------------------------------------------------------------
# Move one job along
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Update a status")

job = st.selectbox("Job", jobs, format_func=lambda j: f"{job_label(j)} · {j['status']}")
buttons = st.columns(len(ADVANCE_TO))
for col, status in zip(buttons, ADVANCE_TO):
    with col:
        if st.button(
            status.capitalize(),
            width="stretch",
            disabled=job["status"] == status,
        ):
            set_status(job["id"], status)
            st.rerun()

# ---------------------------------------------------------------------------
# Delete. The Intake page removes jobs still queued and the Queue page removes
# ones awaiting review; this reaches the rest — approved, submitted, rejected,
# discarded — which are only ever visible from here.
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Delete")
st.caption(
    "Removes the job and every application generated for it. A status of "
    "'discarded' or 'rejected' keeps the record for the table above — delete "
    "is for rows that should not exist at all."
)

if st.session_state.get("confirm_delete") == job["id"]:
    st.warning(f"Delete {job_label(job)} and its applications? This cannot be undone.")
    yes, no, _ = st.columns([1, 1, 4])
    if yes.button("Delete it", type="primary", width="stretch"):
        removed = delete_job(job["id"])
        st.session_state.pop("confirm_delete", None)
        flash(
            "info",
            f"Deleted job #{job['id']} and {removed} application(s). "
            f"Any PDFs are still under out/{job['id']}/.",
        )
        st.rerun()
    if no.button("Cancel", width="stretch"):
        st.session_state.pop("confirm_delete", None)
        st.rerun()
elif st.button(f"❌ Delete job #{job['id']}"):
    st.session_state["confirm_delete"] = job["id"]
    st.rerun()

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Export")

if st.button("📤 Export to Excel", type="primary"):
    frame = pd.DataFrame(rows(with_application=True))
    EXPORT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(EXPORT, index=False)
    st.success(f"Wrote {len(frame)} row(s) to {EXPORT.relative_to(Path.cwd()) if EXPORT.is_relative_to(Path.cwd()) else EXPORT}")

if EXPORT.is_file():
    st.download_button(
        "⬇️ Download applications.xlsx",
        EXPORT.read_bytes(),
        file_name="applications.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
