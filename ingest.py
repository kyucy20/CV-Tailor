"""Pull a job posting off the web so it does not have to be pasted by hand.

Three routes, chosen by domain:

  1. An ATS with a public JSON API (Greenhouse, Lever, Ashby, Workable).
     Always preferred: the API gives structured fields, and reading a
     documented endpoint is not scraping.
  2. Any other domain: fetch the page and let trafilatura find the article
     text — but only after robots.txt says we may.
  3. LinkedIn, Indeed, StepStone: refused without a request being made.
     Their terms prohibit automated access, and a job board that blocks you
     mid-session is worse than one that never started.

Nothing here writes to the database. fetch_job returns a dict and the caller
decides whether to keep it — the confirmation step in the UI is the point,
because extraction is the part most likely to be quietly wrong.
"""

from __future__ import annotations

import re
import urllib.robotparser
from html import unescape
from urllib.parse import urlparse

import httpx
import trafilatura

TIMEOUT = 15.0
RETRIES = 1

# A real browser string. Sending python-httpx/x.y invites a 403 from sites
# that are perfectly happy to serve the same page to a person.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Domains we will not touch. Each prohibits automated access in its terms;
# the entry is a refusal, not a difficulty to be worked around.
BLOCKED = {
    "linkedin.com": "LinkedIn prohibits automated access in its terms of service",
    "indeed.com": "Indeed prohibits automated access in its terms of service",
    "stepstone.de": "StepStone prohibits automated access in its terms of service",
}

MIN_USEFUL = 200  # matches the intake minimum; shorter means extraction failed

# Hard ceiling on a fetched description. Applies to every route — ATS APIs
# included — but never to text pasted by hand, which is yours to judge.
# Postings routinely run 600-900 words, so this does cut most of them; see
# _cap_words for what goes first.
MAX_WORDS = 350


class Result:
    """A fetch outcome: the job dict, or None plus the reason why not."""

    __slots__ = ("job", "reason")

    def __init__(self, job: dict | None, reason: str = ""):
        self.job = job
        self.reason = reason


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
_BREAKS = re.compile(r"(?i)<(br\s*/?|/p|/div|/li|/h[1-6])\s*>")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(value: str | None) -> str:
    """Plain text out of an HTML fragment, keeping paragraph breaks.

    The ATS APIs return description bodies as HTML (Greenhouse double-escapes
    it). Feeding those tags to the model wastes tokens and reads badly in the
    confirmation box, so they come off here.
    """
    if not value:
        return ""
    text = unescape(value)
    if "<" in text:
        text = _BREAKS.sub("\n", text)
        text = _TAGS.sub("", text)
        text = unescape(text)          # entities can survive one pass
    lines = [line.strip() for line in text.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


_WORD = re.compile(r"\S+")

# Headings that open the section saying what the candidate must have — the
# skills and the degree. In a German posting this is "Ihr Profil"; in an
# English one "Requirements" or "What you'll bring". This is the section the
# tailoring actually matches against, so it is kept before anything else.
PROFILE_HEAD = re.compile(
    r"(?i)^\W*"
    r"(?:(?:ihr|ihre|dein|deine|your)\s+)?"
    r"(profil|profile|qualifikationen?|qualifications?|anforderungen|"
    r"requirements?|voraussetzungen|skills?|kenntnisse|erfahrung|experience|"
    r"studium|education|ausbildung|"
    r"was\s+(?:du|sie|wir)\s+\w+|what\s+you[’']?\w*\s+\w+|who\s+you\s+are|"
    r"about\s+you|minimum\s+qualifications|preferred\s+qualifications|"
    r"basic\s+qualifications|your\s+background)"
    r"\b.{0,48}$"
)

# Headings that end it. Perks and company boilerplate are the first things
# worth losing when the budget is tight.
OTHER_HEAD = re.compile(
    r"(?i)^\W*"
    r"(wir\s+bieten|was\s+wir\s+(?:dir\s+|ihnen\s+)?bieten|unser\s+angebot|"
    r"we\s+offer|what\s+we\s+offer|benefits?|perks|vorteile|"
    r"über\s+uns|about\s+us|unternehmen|company|our\s+team|"
    r"bewerbung|how\s+to\s+apply|apply|diversity|equal\s+opportunity|"
    r"datenschutz|privacy)"
    r"\b.{0,48}$"
)


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _first_line(block: str) -> str:
    stripped = block.strip()
    return stripped.splitlines()[0].strip() if stripped else ""


def _is_heading(block: str) -> bool:
    """Whether a block opens a new section, by shape rather than wording.

    Needed for the headings not in either pattern — "Ihre Aufgaben",
    "Responsibilities", "The role". Without this a section under an unknown
    heading inherits the tier of whatever came before it, so the tasks
    following an "Über uns" get treated as boilerplate and dropped.
    """
    line = _first_line(block)
    if not line or len(line) > 64:
        return False
    if line.endswith((".", "!", "?", ",", ";")):
        return False
    return word_count(line) <= 8


def _cap_words(text: str, limit: int = MAX_WORDS) -> str:
    """Trim to `limit` words, keeping the profile/requirements section.

    A plain head-truncation would spend the whole budget on "about us" and
    drop the part listing the skills and the degree — which is the part the
    CV is being matched against. So the section under a PROFILE_HEAD heading
    is reserved first, and whatever budget is left is filled from the top of
    the posting. Paragraphs come back in their original order.

    Falls back to a head-truncation when no such heading is found, which is
    the best available guess for an unstructured page.
    """
    if word_count(text) <= limit:
        return text

    blocks = re.split(r"\n\s*\n", text)
    if len(blocks) < 2:
        return _head(text, limit)

    # Three tiers, by the heading each block sits under:
    #   2  the profile/requirements section — the skills and the degree
    #   1  anything unlabelled, in practice the tasks and the intro
    #   0  perks, "about us", legal — the first things worth losing
    PROFILE, NEUTRAL, BOILERPLATE = 2, 1, 0
    tier = [NEUTRAL] * len(blocks)
    current = NEUTRAL
    for i, block in enumerate(blocks):
        head = _first_line(block)
        if PROFILE_HEAD.match(head):
            current = PROFILE
        elif OTHER_HEAD.match(head):
            current = BOILERPLATE
        elif _is_heading(block):
            # An unrecognised heading — tasks, "the role". Back to neutral, so
            # it does not inherit the tier of the section above it.
            current = NEUTRAL
        tier[i] = current

    if PROFILE not in tier:
        return _head(text, limit)

    keep, spent = set(), 0
    for wanted in (PROFILE, NEUTRAL, BOILERPLATE):
        for i, t in enumerate(blocks):
            if tier[i] != wanted:
                continue
            size = word_count(blocks[i])
            if spent + size > limit:
                continue          # skip, do not stop: a later block may fit
            keep.add(i)
            spent += size

    if not keep:
        return _head(text, limit)
    return "\n\n".join(blocks[i] for i in sorted(keep))


def _head(text: str, limit: int) -> str:
    """The first `limit` words, cut on a word boundary.

    Cuts by offset rather than splitting and rejoining, so paragraph breaks
    in the kept portion survive.
    """
    words = list(_WORD.finditer(text))
    if len(words) <= limit:
        return text
    return text[: words[limit - 1].end()].rstrip()


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _blocked_reason(host: str) -> str | None:
    for blocked, reason in BLOCKED.items():
        if host == blocked or host.endswith("." + blocked):
            return reason
    return None


def _client() -> httpx.Client:
    # One retry, at the transport layer, so a single dropped connection does
    # not turn into a failed fetch.
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,de;q=0.8"},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=httpx.HTTPTransport(retries=RETRIES),
    )


def _get(url: str, **kwargs) -> httpx.Response | None:
    try:
        with _client() as client:
            response = client.get(url, **kwargs)
        return response if response.status_code == 200 else None
    except httpx.HTTPError:
        return None


def robots_allow(url: str) -> bool:
    """Whether robots.txt permits fetching this URL.

    A robots.txt we cannot read is treated as permission: that is what the
    standard says, and the alternative is refusing every site whose server
    happens to 404 the file.
    """
    parsed = urlparse(url)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    response = _get(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    if response is None:
        return True
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------------------
# ATS adapters. Each takes the parsed URL path and returns a job dict or None.
# ---------------------------------------------------------------------------

def _greenhouse(url: str) -> dict | None:
    # boards.greenhouse.io/<token>/jobs/<id> — job-boards.greenhouse.io too.
    m = re.search(r"/(?:embed/job_app\?for=)?([\w.-]+)/jobs/(\d+)", urlparse(url).path)
    if not m:
        m = re.search(r"for=([\w.-]+).*?gh_jid=(\d+)", url)
    if not m:
        return None
    token, job_id = m.group(1), m.group(2)

    response = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
    if response is None:
        return None
    data = response.json()

    company = ""
    board = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}")
    if board is not None:
        company = board.json().get("name", "")

    return {
        "company": company or token,
        "role_title": data.get("title", ""),
        "job_location": (data.get("location") or {}).get("name", ""),
        "description": strip_html(data.get("content")),
        "source": "greenhouse",
    }


def _lever(url: str) -> dict | None:
    # jobs.lever.co/<company>/<uuid>
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        return None
    company, posting = parts[0], parts[1]

    response = _get(f"https://api.lever.co/v0/postings/{company}/{posting}?mode=json")
    if response is None:
        return None
    data = response.json()

    # Lever splits the body: an intro, then titled lists, then a closing.
    body = [data.get("descriptionPlain") or strip_html(data.get("description"))]
    for block in data.get("lists") or []:
        body.append(strip_html(block.get("text")))
        body.append(strip_html(block.get("content")))
    body.append(data.get("additionalPlain") or strip_html(data.get("additional")))

    categories = data.get("categories") or {}
    return {
        "company": company,
        "role_title": data.get("text", ""),
        "job_location": categories.get("location", ""),
        "description": "\n\n".join(p for p in body if p),
        "source": "lever",
    }


def _ashby(url: str) -> dict | None:
    # jobs.ashbyhq.com/<org>/<job-uuid>
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        return None
    org, job_id = parts[0], parts[1]

    response = _get(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}",
        params={"includeCompensation": "true"},
    )
    if response is None:
        return None

    jobs = response.json().get("jobs") or []
    match = next((j for j in jobs if str(j.get("id")) == job_id), None)
    if match is None:
        return None

    description = (
        match.get("descriptionPlain")
        or strip_html(match.get("descriptionHtml"))
        or strip_html(match.get("description"))
    )
    return {
        "company": match.get("organizationName") or org,
        "role_title": match.get("title", ""),
        "job_location": match.get("location") or "",
        "description": description,
        "source": "ashby",
    }


def _workable(url: str) -> dict | None:
    # apply.workable.com/<account>/j/<shortcode>/
    m = re.search(r"/([\w.-]+)/j/([A-Z0-9]+)", urlparse(url).path, re.I)
    if not m:
        return None
    account, shortcode = m.group(1), m.group(2)

    response = _get(
        f"https://apply.workable.com/api/v1/accounts/{account}/jobs/{shortcode}"
    )
    if response is None:
        return None
    data = response.json()

    location = data.get("location") or {}
    if isinstance(location, dict):
        location = ", ".join(
            str(v) for k, v in location.items()
            if k in ("city", "region", "country") and v
        )

    body = "\n\n".join(
        strip_html(data.get(k))
        for k in ("description", "requirements", "benefits")
        if data.get(k)
    )
    return {
        "company": (data.get("account") or {}).get("name") or account,
        "role_title": data.get("title", ""),
        "job_location": location or "",
        "description": body,
        "source": "workable",
    }


ATS = (
    (("boards.greenhouse.io", "job-boards.greenhouse.io"), _greenhouse),
    (("jobs.lever.co",), _lever),
    (("jobs.ashbyhq.com",), _ashby),
    (("apply.workable.com",), _workable),
)


# ---------------------------------------------------------------------------
# generic pages
# ---------------------------------------------------------------------------

def _generic(url: str) -> Result:
    if not robots_allow(url):
        return Result(None, f"robots.txt on {domain_of(url)} disallows fetching this page")

    response = _get(url)
    if response is None:
        return Result(None, f"could not fetch {domain_of(url)} (timeout, refused, or non-200)")

    text = trafilatura.extract(
        response.text,
        include_comments=False,
        include_tables=True,
        favor_recall=True,        # job pages are lists more than prose
    )
    if not text or len(text.strip()) < MIN_USEFUL:
        got = len(text.strip()) if text else 0
        return Result(
            None,
            f"fetched {domain_of(url)} but only extracted {got} characters — "
            f"the posting is probably rendered by JavaScript. Paste it instead.",
        )

    return Result({
        "company": "",
        "role_title": "",
        "job_location": "",
        "description": text.strip(),
        "source": domain_of(url),
    })


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def fetch(url: str) -> Result:
    """fetch_job, but carrying the reason a fetch produced nothing.

    The UI needs to tell a blocked domain apart from a robots.txt refusal and
    from a dead link, so the reason has to survive. fetch_job below is the
    plain dict-or-None form.
    """
    url = (url or "").strip()
    if not url:
        return Result(None, "no URL given")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    host = domain_of(url)
    if not host:
        return Result(None, f"{url!r} is not a URL")

    blocked = _blocked_reason(host)
    if blocked:
        # Returns before any request is made.
        return Result(None, f"{host} is not fetched: {blocked}. Paste the posting instead.")

    for hosts, adapter in ATS:
        if host in hosts:
            job = adapter(url)
            if job is None:
                return Result(None, f"the {host} API did not return this posting")
            if len(job["description"]) < MIN_USEFUL:
                return Result(None, f"{host} returned a posting with almost no description")
            return Result(_capped(job))

    result = _generic(url)
    return Result(_capped(result.job), result.reason) if result.job else result


def _capped(job: dict) -> dict:
    """Apply MAX_WORDS to a job dict on its way out of fetch().

    One place rather than per-adapter: every route leaves through here, so
    the cap cannot be missed by a new adapter. The length checks above run on
    the full text, so a long posting is never rejected for being short after
    its own truncation.
    """
    job["description"] = _cap_words(job["description"])
    return job


def fetch_job(url: str) -> dict | None:
    """The posting at `url` as {company, role_title, job_location,
    description, source}, or None if it cannot be fetched."""
    return fetch(url).job
