"""Put one job posting into the queue.

    python intake.py posting.txt
    python intake.py posting.txt https://example.com/jobs/123

Prints the new job id, or "duplicate" if the same posting is already stored.
Deduplication is on the description text, whitespace- and case-normalised, so
the same posting saved twice under different file names is caught.

A placeholder for the Streamlit intake page: same call, nicer front door.
"""

from __future__ import annotations

import sys
from pathlib import Path

from db import add_job, init_db


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python intake.py <posting.txt> [url]")

    path = Path(sys.argv[1])
    url = sys.argv[2] if len(sys.argv) > 2 else ""
    if not path.exists():
        sys.exit(f"no such file: {path}")

    description = path.read_text(encoding="utf-8").strip()
    if not description:
        sys.exit(f"{path} is empty — nothing to queue")

    init_db()
    job_id = add_job(description, url=url)
    if job_id is None:
        print("duplicate")
    else:
        print(job_id)


if __name__ == "__main__":
    main()
