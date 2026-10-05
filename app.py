"""Intake — get a job posting into the queue, by URL or by paste.

    streamlit run app.py

Two ways in, one way out: whatever ends up in the description box is what
gets saved, through a single add_job call at the bottom of this file. Fetching
only fills the box — it never saves on your behalf, because extraction is the
step most likely to be quietly wrong, and a posting that lost half its
requirements produces a confident CV tailored to nothing.
"""

from __future__ import annotations

import streamlit as st

import batch
from db import add_job, delete_job, get_jobs, set_job_meta
from ingest import MAX_WORDS, domain_of, fetch, word_count
from ui import APPLY_ROUTES, bootstrap, flash, job_label, profile, show_flashes

# The shortest posting worth generating against, applied to the saved text no
# matter which route it came in by.
MIN_DESCRIPTION = 200


# Widget state cleared after a successful save. Done at the top of a run,
# before the widgets exist: Streamlit refuses edits to a widget's key once
# that widget has been instantiated.
RESETTABLE = ("url", "description", "fetched", "fetch_error")

st.set_page_config(page_title="Intake", page_icon="📥", layout="centered")
bootstrap()

if st.session_state.pop("_reset", False):
    for key in RESETTABLE:
        st.session_state.pop(key, None)

st.title("📥 Intake")
st.caption("Fetch a posting or paste one. Nothing reaches the model until you press Generate.")

show_flashes()

# ---------------------------------------------------------------------------
# By URL. Outside the form, so fetching does not clear what you have typed.
# ---------------------------------------------------------------------------

url = st.text_input(
    "Job URL",
    key="url",
    placeholder="https://boards.greenhouse.io/… · jobs.lever.co/… · or any careers page",
)
description_now = st.session_state.get("description", "")

if url.strip() and not description_now.strip():
    if st.button(f"🔗 Fetch from {domain_of(url) or 'URL'}"):
        with st.spinner("Fetching…"):
            result = fetch(url)
        if result.job:
            st.session_state["description"] = result.job["description"]
            st.session_state["fetched"] = result.job
            st.session_state.pop("fetch_error", None)
        else:
            st.session_state["fetch_error"] = result.reason
            st.session_state.pop("fetched", None)
        st.rerun()
elif url.strip() and description_now.strip():
    st.caption("Clear the description box to fetch this URL instead.")

if reason := st.session_state.get("fetch_error"):
    st.error(f"Could not fetch it: {reason}")
    st.caption("The paste box below still works — that path is unaffected.")

fetched = st.session_state.get("fetched")
if fetched:
    st.success(f"Fetched via **{fetched['source']}**. Check it below, then add it to the queue.")
    a, b, c = st.columns(3)
    a.caption(f"**Company**\n\n{fetched['company'] or '—'}")
    b.caption(f"**Role**\n\n{fetched['role_title'] or '—'}")
    c.caption(f"**Location**\n\n{fetched['job_location'] or '—'}")
    words = word_count(fetched["description"])
    st.caption(
        f"{words} words extracted — read them before saving. "
        f"Edit anything wrong in the box below; your edits are what get stored."
    )
    if words >= MAX_WORDS:
        st.warning(
            f"Cut to the {MAX_WORDS}-word limit. Postings put boilerplate first and "
            f"requirements last, so the part that was dropped is often the part worth "
            f"matching against. Paste the rest in below if this one looks short."
        )

# ---------------------------------------------------------------------------
# The form. The description box is shared: fetched text lands here, pasted
# text is typed here, and only what is here at submit time is ever saved.
# ---------------------------------------------------------------------------

with st.form("intake", clear_on_submit=True):
    apply_type = st.radio("Apply route", APPLY_ROUTES, horizontal=True)
    description = st.text_area(
        "Job description",
        key="description",
        height=320,
        placeholder="Paste the full posting — responsibilities, requirements, everything.",
    )
    submitted = st.form_submit_button("Add to queue", type="primary")

if submitted:
    text = description.strip()
    if len(text) < MIN_DESCRIPTION:
        st.error(
            f"That posting is {len(text)} characters — too short to tailor against "
            f"(minimum {MIN_DESCRIPTION}).\n\n"
            f"A truncated paste is the most common way a bad CV gets generated: "
            f"the model fills the gaps instead of matching the posting. Paste the "
            f"whole thing, requirements included."
        )
    else:
        # Credit the fetch only if the stored text is still the fetched text.
        # Edit it into something else and this was, honestly, a paste.
        meta = st.session_state.get("fetched")
        from_fetch = bool(meta) and text == meta["description"].strip()
        source = meta["source"] if from_fetch else "paste"

        job_id = add_job(text, url=url.strip(), apply_type=apply_type, source=source)
        if job_id is None:
            st.warning("Duplicate — this posting is already in the queue.")
        else:
            # What the ATS already told us, so the Queue page has a company
            # and a role to show before generation ever runs. Generation
            # overwrites these with what the model reads off the text.
            if from_fetch:
                set_job_meta(
                    job_id, meta["company"], meta["role_title"], meta["job_location"]
                )
            flash("success", f"Queued as job #{job_id} (source: {source}).")
            st.session_state["_reset"] = True
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# The batch. Behind a button, never on rerun.
# ---------------------------------------------------------------------------

queued = get_jobs("new")
st.subheader(f"Queue: {len(queued)} job(s) waiting")

if queued:
    pending = st.session_state.get("confirm_delete")
    for job in queued:
        line, action = st.columns([9, 1])
        line.write(f"• #{job['id']} — {job['description'][:80].strip()}…")
        if pending == job["id"]:
            # Two clicks, because this is not undoable and the rows sit close
            # enough together to hit the wrong one.
            yes, no = action.columns(2)
            if yes.button("✔", key=f"del_yes_{job['id']}", help="Confirm removal"):
                delete_job(job["id"])
                st.session_state.pop("confirm_delete", None)
                flash("info", f"Removed job #{job['id']}.")
                st.rerun()
            if no.button("✖", key=f"del_no_{job['id']}", help="Keep it"):
                st.session_state.pop("confirm_delete", None)
                st.rerun()
        elif action.button("🗑", key=f"del_{job['id']}", help="Remove from the queue"):
            st.session_state["confirm_delete"] = job["id"]
            st.rerun()

    if st.button(f"Generate all {len(queued)}", type="primary"):
        prof = profile()
        done, failed = 0, []
        progress = st.progress(0.0)
        with st.spinner("Calling the model and rendering PDFs…"):
            for i, job in enumerate(queued, 1):
                try:
                    batch.process(prof, job)
                    done += 1
                except Exception as e:
                    # One bad posting must not strand the rest of the queue.
                    # The job keeps status 'new' and is retried next run.
                    failed.append((job["id"], f"{type(e).__name__}: {e}"))
                progress.progress(i / len(queued))

        # Through flash(), not st.success/st.error: the st.rerun() below
        # discards anything drawn in this run, which is how a failed batch
        # used to look identical to no click at all.
        if done:
            flash("success", f"{done} generated — review them on the Queue page.")
        for job_id, message in failed:
            flash("error", f"job #{job_id} failed — {message}")
        if failed:
            flash(
                "info",
                f"{len(failed)} job(s) kept status 'new' and will be retried "
                f"the next time you press Generate.",
            )
        st.rerun()
else:
    st.info("Nothing waiting. Fetch or paste a posting above.")

generated = get_jobs("generated")
if generated:
    st.divider()
    st.caption(f"{len(generated)} awaiting review on the Queue page:")
    for job in generated[:5]:
        st.caption(f"• {job_label(job)}")
