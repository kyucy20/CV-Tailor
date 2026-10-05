"""Shared plumbing for the Streamlit pages.

Streamlit reruns a page top to bottom on every click, so anything expensive
has to survive across reruns. Two things do, both via @st.cache_resource:
the SQLite connection and the parsed CV profile.

Nothing here talks SQL. The connection is opened once and handed to db, so
the pages keep calling db functions and the module decides what to do with
it — see db.use_connection.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import streamlit as st

import db
from renderer import load_profile

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

# How an application is submitted. Shared so the Intake form and the
# Tracker's editable table offer the same options.
APPLY_ROUTES = ("Company site", "Easy Apply")


@st.cache_resource
def _connection() -> sqlite3.Connection:
    """One connection for the life of the server process.

    check_same_thread=False because Streamlit may run a rerun on a different
    thread than the one that opened this, and the default would refuse.
    Serialising access is left to SQLite itself, which is safe for the one
    writer this app ever has.
    """
    conn = sqlite3.connect(db.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_resource
def profile():
    """The parsed CV. Read-only, so one copy is shared by every session."""
    return load_profile()


def bootstrap() -> None:
    """Call once at the top of every page, before any db call."""
    db.use_connection(_connection())
    db.init_db()


def flash(kind: str, message: str) -> None:
    """Queue a message to show after the next st.rerun().

    st.rerun() throws away everything the current run drew, so a message
    written just before one is never seen. Anything reported at the end of a
    button branch has to go through here instead.
    """
    st.session_state.setdefault("flash", []).append((kind, message))


def show_flashes() -> None:
    """Draw and clear whatever flash() queued. Call once per page, at the top."""
    for kind, message in st.session_state.pop("flash", []):
        getattr(st, kind)(message)


def job_label(job: dict) -> str:
    """One-line description of a job for a selectbox or a header."""
    bits = [b for b in (job.get("company"), job.get("role_title")) if b]
    return f"#{job['id']} · " + (" — ".join(bits) if bits else "untitled")


def bullet_text(prof, bullet_id: str, lang: str, angle: str) -> str:
    """The variant text that actually went on the page for this bullet.

    Returns a readable marker instead of raising: this is used for checkbox
    labels, and a CV.json edit that drops a bullet must not take the whole
    review page down with it.
    """
    try:
        return prof.find_bullet(bullet_id).variant(lang, angle).text
    except (KeyError, LookupError) as e:
        return f"[unavailable: {e}]"


# Table columns a person may correct, mapped to nothing in particular here —
# apply_job_edits knows which db setter each one belongs to.
JOB_EDIT_FIELDS = ("status", "company", "role", "location", "apply_route")


def diff_job_rows(original: list[dict], edited: list[dict]) -> list[dict]:
    """Which rows changed, and in which fields.

    Compared as strings: the grid hands back numpy scalars and None where the
    database had NULL, and "" != None would report an edit on every row that
    was ever empty.
    """
    changes = []
    for before, after in zip(original, edited):
        fields = {
            f for f in JOB_EDIT_FIELDS
            if str(before.get(f) or "") != str(after.get(f) or "")
        }
        if fields:
            changes.append({"id": before["id"], "after": after, "fields": fields})
    return changes


def apply_job_edits(changes: list[dict]) -> int:
    """Write the edits through db setters — no SQL in the UI layer.

    Returns how many jobs were written. Raises whatever the setters raise:
    ValueError for a status outside STATUSES, KeyError for a job that was
    deleted in another tab between the read and the save.
    """
    for change in changes:
        job_id, after, fields = change["id"], change["after"], change["fields"]
        if "status" in fields:
            db.set_status(job_id, after["status"])
        if fields & {"company", "role", "location"}:
            # set_job_meta writes all three columns, so send the edited value
            # for all three rather than only the one that differs.
            db.set_job_meta(
                job_id,
                after["company"] or "",
                after["role"] or "",
                after["location"] or "",
            )
        if "apply_route" in fields:
            db.set_apply_type(job_id, after["apply_route"] or "")
    return len(changes)


def safe_filename(part: str) -> str:
    """A company name made safe to put in a download filename.

    Company names carry characters a filesystem will not take — "Meta / AI",
    "Yahoo!: Inc" — and a stray slash turns a filename into a path. Collapses
    whitespace too, so the result stays one tidy line.
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", part or "")
    return re.sub(r"\s+", " ", cleaned).strip(" .")


def read_pdf(path: str | None) -> bytes | None:
    """PDF bytes for a download button, or None if the file is gone."""
    if not path:
        return None
    p = Path(path)
    return p.read_bytes() if p.is_file() else None
