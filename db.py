import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from schema import Selection

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "jobs.db"

# The full lifecycle of a job, in the order it normally moves through. The
# column is a plain TEXT with no CHECK constraint, so set_status is the only
# thing standing between a typo and a job that no query will ever find again.
STATUSES = (
    "new",
    "generated",
    "approved",
    "submitted",
    "interview",
    "rejected",
    "discarded",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    url          TEXT,
    company      TEXT,
    role_title   TEXT,
    job_location TEXT,
    description  TEXT NOT NULL,
    apply_type   TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    fingerprint  TEXT UNIQUE,
    source       TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id                     INTEGER PRIMARY KEY,
    job_id                 INTEGER NOT NULL REFERENCES jobs(id),
    summary_id             TEXT,
    bullet_ids             TEXT,
    lang                   TEXT,
    angle                  TEXT,
    cover_letter           TEXT,
    unmatched_requirements TEXT,
    prompt_version         TEXT,
    model                  TEXT,
    cv_path                TEXT,
    letter_path            TEXT,
    created_at             TEXT NOT NULL
);
"""


_shared: sqlite3.Connection | None = None


def use_connection(conn: sqlite3.Connection | None) -> None:
    """Route every call in this module through one caller-supplied connection.

    Streamlit reruns the whole script on each interaction and can serve those
    reruns from different threads, so the UI opens a single connection with
    check_same_thread=False, caches it, and hands it here — which keeps SQL
    out of the UI layer while still sharing one connection. Pass None to go
    back to a fresh connection per call, which is what the CLI scripts want.
    """
    global _shared
    if conn is not None:
        # Every function here returns dicts built from sqlite3.Row. Set it on
        # the caller's behalf rather than trusting them to: the failure is a
        # TypeError from inside dict(), which points nowhere near the cause.
        conn.row_factory = sqlite3.Row
    _shared = conn


def get_conn() -> sqlite3.Connection:
    if _shared is not None:
        return _shared
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Columns added after the first databases were created. CREATE TABLE IF NOT
# EXISTS leaves an existing table alone, so a new column has to be added by
# hand or it exists only for people who started later.
MIGRATIONS = (
    ("jobs", "source", "ALTER TABLE jobs ADD COLUMN source TEXT"),
)


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for table, column, statement in MIGRATIONS:
            present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                conn.execute(statement)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(description: str) -> str:
    return sha256(" ".join(description.split()).lower().encode()).hexdigest()


def add_job(
    description: str, url: str = "", apply_type: str = "", source: str = "paste"
) -> int | None:
    """Returns the new job id, or None if this description is already stored.

    `source` records where the text came from — an ATS name ("greenhouse"),
    a bare domain for a scraped page, or "paste" when it was typed in. The
    fingerprint is over the description alone, so the same posting fetched
    from its ATS and pasted by hand still counts as one job.
    """
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO jobs"
                " (url, description, apply_type, source, fingerprint, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (url, description, apply_type, source, _fingerprint(description), _now()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_jobs(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM jobs"
    params = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY created_at DESC"

    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def get_job(job_id: int) -> dict | None:
    """Returns the job row, or None if there is no job with this id."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def set_status(job_id: int, status: str) -> None:
    """Moves a job to a new status. Raises ValueError for a status outside
    STATUSES and KeyError for a job id that does not exist — a silent no-op
    on a mistyped id is the kind of thing you only notice a week later."""
    if status not in STATUSES:
        raise ValueError(
            f"unknown status {status!r}; must be one of {', '.join(STATUSES)}"
        )
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?", (status, job_id)
        )
        if cur.rowcount == 0:
            raise KeyError(f"no job with id {job_id}")


def set_job_meta(
    job_id: int, company: str = "", role_title: str = "", job_location: str = ""
) -> None:
    """Fills in what the model read off the posting.

    add_job stores only the raw description — the company, role and location
    are not known until a Selection comes back — so these three columns stay
    empty until generation fills them in here.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET company = ?, role_title = ?, job_location = ?"
            " WHERE id = ?",
            (company, role_title, job_location, job_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"no job with id {job_id}")


def set_apply_type(job_id: int, apply_type: str) -> None:
    """How this one is applied for — company site, Easy Apply, whatever you
    type. Free text by design: unlike status, nothing downstream branches on
    it, so a new route does not need a code change."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET apply_type = ? WHERE id = ?", (apply_type, job_id)
        )
        if cur.rowcount == 0:
            raise KeyError(f"no job with id {job_id}")


def delete_job(job_id: int) -> int:
    """Delete a job and every application generated for it.

    Returns how many application rows went with it. Both deletes share a
    transaction, so a bad id cannot take the applications while leaving the
    job — it raises KeyError and nothing is removed.

    The PDFs under out/<job_id>/ are deliberately left on disk. This module
    does not own the filesystem, and an orphaned PDF costs a few hundred
    kilobytes where a deleted one costs a regeneration.

    Note this also frees the description's fingerprint, so the same posting
    can be added again afterwards — which is usually the point.
    """
    with get_conn() as conn:
        apps = conn.execute(
            "DELETE FROM applications WHERE job_id = ?", (job_id,)
        ).rowcount
        if conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount == 0:
            raise KeyError(f"no job with id {job_id}")
        return apps


def save_application(
    job_id: int,
    selection: Selection,
    cv_path: str,
    letter_path: str,
    model: str = "",
    prompt_version: str = "",
) -> int:
    """Stores one generated application and moves its job to 'generated'.

    Both statements share a transaction: a job is never left claiming to be
    'generated' without the row that backs the claim, and an application row
    never exists under a job still queued as 'new'.

    The two list fields are JSON-encoded on the way in; latest_application
    decodes them again, so no caller outside this module ever handles the
    encoded form.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO applications ("
            " job_id, summary_id, bullet_ids, lang, angle, cover_letter,"
            " unmatched_requirements, prompt_version, model, cv_path,"
            " letter_path, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                selection.summary_id,
                json.dumps(selection.bullet_ids),
                selection.lang,
                selection.angle,
                selection.cover_letter,
                json.dumps(selection.unmatched_requirements),
                prompt_version,
                model,
                cv_path,
                letter_path,
                _now(),
            ),
        )
        updated = conn.execute(
            "UPDATE jobs SET status = 'generated' WHERE id = ?", (job_id,)
        )
        if updated.rowcount == 0:
            # SQLite does not enforce the REFERENCES clause unless
            # foreign_keys is switched on per connection, so without this the
            # insert above would quietly leave an application under no job.
            # Raising rolls the whole block back.
            raise KeyError(f"no job with id {job_id}")
        return cur.lastrowid


def latest_application(job_id: int) -> dict | None:
    """The most recent application for this job, or None if it has none.

    `bullet_ids` and `unmatched_requirements` come back as real lists. A row
    written before those columns were populated decodes to an empty list
    rather than None, so callers can iterate without checking first.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    if row is None:
        return None

    app = dict(row)
    for field in ("bullet_ids", "unmatched_requirements"):
        app[field] = json.loads(app[field]) if app[field] else []
    return app