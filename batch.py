"""Generate tailored applications for every job queued as 'new'.

    python batch.py

Each job gets its own directory under out/<job_id>/, holding the tailored CV
and the cover letter, and one row in the applications table. Saving the row
flips the job to 'generated', so a second run picks up only what arrived
since the first.

A job that fails is reported and skipped; its status stays 'new', so fixing
the cause and re-running retries it. This replaces apply.py, which tailored
one posting read from job.txt.
"""

from __future__ import annotations

from pathlib import Path

from db import get_jobs, init_db, save_application, set_job_meta
from generate import MODEL, generate
from renderer import letter_header, load_profile, render, render_cover_letter

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def job_dir(job_id: int) -> Path:
    d = OUT / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel(path: Path) -> str:
    """Project-relative path for display, falling back to the full path.

    Only ever used for printing, so it must not raise: everything it reports
    on has already been written and committed, and a job that finished is not
    allowed to be reported as failed because a path looked unusual.
    """
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)


def process(profile, job: dict) -> None:
    """Generate, render and store one job. Raises on any failure."""
    job_id = job["id"]
    sel = generate(profile, job["description"])

    out_dir = job_dir(job_id)
    cv_path = out_dir / "cv.pdf"
    letter_path = out_dir / "cover_letter.pdf"

    render(profile, sel.summary_id, sel.bullet_ids, sel.lang, sel.angle, str(cv_path))
    recipient, subject = letter_header(sel)
    render_cover_letter(
        profile, sel.cover_letter, sel.lang, str(letter_path),
        recipient=recipient, subject=subject,
    )

    # Last, and only once both PDFs exist: the row and the 'generated' status
    # are a claim that the files are on disk.
    set_job_meta(job_id, sel.company, sel.role_title, sel.job_location)
    app_id = save_application(job_id, sel, str(cv_path), str(letter_path), model=MODEL)

    label = " / ".join(p for p in (sel.company, sel.role_title) if p) or "untitled"
    print(f"  {label}")
    print(f"    {sel.lang}/{sel.angle}, {len(sel.bullet_ids)} bullets, application #{app_id}")
    print(f"    {_rel(cv_path)}")
    print(f"    {_rel(letter_path)}")
    if sel.unmatched_requirements:
        print(f"    unmatched: {len(sel.unmatched_requirements)}")


def main() -> None:
    init_db()
    jobs = get_jobs("new")
    if not jobs:
        print("nothing queued — no jobs with status 'new'")
        return

    print(f"{len(jobs)} job(s) queued\n")
    profile = load_profile()

    done, failed = 0, []
    for job in jobs:
        print(f"job {job['id']} ({len(job['description'].split())} words)")
        try:
            process(profile, job)
            done += 1
        except Exception as e:
            # One bad posting must not strand the rest of the queue. The job
            # keeps status 'new', so it is retried on the next run.
            failed.append(job["id"])
            print(f"    FAILED: {type(e).__name__}: {e}")
        print()

    print(f"{done} succeeded, {len(failed)} failed" + (f" (jobs {failed})" if failed else ""))


if __name__ == "__main__":
    main()
