# CV-Tailor

Tailors one CV to many job postings, without letting a language model write a
word of the CV.

Paste or fetch a posting, and the pipeline picks which of your stored bullet
points best evidence that posting's requirements, renders a one-page PDF and a
matching cover letter, and tracks the application from queued to interview.

## The idea

The model never writes CV text. It returns **bullet ids** — `prj_rag_b1`,
`exp_1_b3` — and the renderer looks each one up in `CV.json` and prints the
stored sentence verbatim. Every id is validated against the real set before
anything is rendered, so an invented id is rejected rather than printed:

```
unknown ids: ['prj_rag_b9']. Every id must come verbatim from the bullet
bank or the summary list; ids may not be invented.
```

The failure is fed back to the model for exactly one retry. The only prose it
writes is the cover letter.

This matters because the usual approach — "rewrite my CV for this job" —
produces fluent text about work you did not do. Here a hallucination can only
ever be a wrong *id*, and a wrong id cannot reach the page.

Every bullet is stored in four variants: **DE/EN × technical/impact**. The
model picks the language from the posting and the angle from its emphasis, and
the renderer substitutes the matching variant.

## What you need

- **Python 3.14** (3.11+ should work; developed on 3.14)
- **[Typst](https://typst.app)** — `brew install typst`. The only layout
  backend; there is no fallback.
- **An Anthropic API key** — [console.anthropic.com](https://console.anthropic.com/settings/keys)
- **Your own `CV.json`** — not in this repo (see below)

```bash
python -m venv .venv
.venv/bin/pip install anthropic pydantic python-dotenv streamlit pandas \
                      openpyxl httpx trafilatura pypdf
```

Create `.env` in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

If your key is organisation-scoped rather than workspace-scoped, add the
workspace it should bill:

```
ANTHROPIC_WORKSPACE_ID=wrkspc_...
```

### CV.json is yours to write

`CV.json` holds personal data and is gitignored, so a fresh clone has no
content to tailor. You supply it. `schema.py` is the specification — it is a
Pydantic model and the error messages are the documentation:

```bash
.venv/bin/python main.py      # validates CV.json, prints the id count
```

The top-level shape is `version`, `meta`, `summaries`, `experience`,
`projects`, `skills`, `education`, `languages`. Each bullet looks like:

```json
{
  "id": "prj_rag_b1",
  "tags": ["rag", "llm", "faiss"],
  "strength": 5,
  "variants": [
    { "Lang": "EN", "angle": "technical", "text": "Built a retrieval-augmented…" },
    { "Lang": "EN", "angle": "impact",    "text": "Gives students answers in…" },
    { "Lang": "DE", "angle": "technical", "text": "Entwicklung eines Retrieval-…" },
    { "Lang": "DE", "angle": "impact",    "text": "Studierende erhalten Antworten…" }
  ]
}
```

A missing language raises rather than falling back across languages — a German
render can never silently emit English.

## Running it

```bash
.venv/bin/streamlit run app.py
```

Use `.venv/bin/streamlit`, not a system-wide `streamlit`. The launcher binds to
its own interpreter, and the wrong one fails on `No module named pydantic`.

Three pages:

| Page | What it does |
| --- | --- |
| **Intake** | Paste a posting, or fetch one from a URL. Generate everything queued. |
| **Queue** | Review a generation: untick bullets, edit the letter, re-render, approve or discard. |
| **Tracker** | Status overview, editable table, Excel export. |

Nothing expensive runs on a rerun. Streamlit re-executes the whole script on
every click, so every model call and every PDF render sits behind a button and
writes its result to the database.

### Or from the command line

```bash
.venv/bin/python intake.py posting.txt [url]   # queue one; prints the id or "duplicate"
.venv/bin/python batch.py                      # generate everything queued
```

`batch.py` reports a failing job and moves on — one bad posting never strands
the queue, and a failed job keeps status `new` so it retries next run.

## Pulling postings off the web

`ingest.py` routes on the URL's domain:

1. **ATS boards** — Greenhouse, Lever, Ashby and Workable, through their public
   JSON APIs. Structured fields, clean text, and the company and role come back
   filled in. Reading a documented endpoint is not scraping.
2. **Anything else** — fetched with a real user-agent and a timeout, main text
   extracted with trafilatura, but only after `robots.txt` permits it.
3. **LinkedIn, Indeed, StepStone** — refused, with no request made. Their terms
   prohibit automated access.

Fetched text is capped at **350 words**, and the cap is section-aware: it
reserves the requirements section (`Ihr Profil`, `Requirements`,
`Qualifications`, `Who you are`) before spending the budget on "about us". A
plain head-truncation would keep the marketing copy and drop the part the CV is
being matched against.

Fetching only fills the box — nothing is saved until you confirm it. Extraction
is the step most likely to be quietly wrong.

> **Tip for LinkedIn postings:** follow "Apply on company website". It usually
> leads to the company's Greenhouse or Lever page, which fetches cleanly.

## How a job moves

```
new ──► generated ──► approved ──► submitted ──► interview
                 └──► discarded              └──► rejected
```

`jobs` rows hold the posting; `applications` rows hold one generation each.
Re-rendering writes a **new** application row rather than overwriting, so a bad
edit costs one click to walk back and the previous PDFs stay on disk.

## Layout

Two Typst templates, both driven by JSON the renderer writes:

- `template.typ` — the CV. Section order, fonts, spacing and rules are all
  constants at the top of the file; change them without touching Python.
- `cover_letter.typ` — the letter, in matching typography.

The only text in either file is structural chrome — headings, date formats,
category labels. Every sentence comes from `CV.json`.

```bash
.venv/bin/python render_test.py   # same selection, EN/impact and DE/technical
```

## File map

| File | Role |
| --- | --- |
| `schema.py` | Pydantic models. `Selection` is what the model is allowed to return. |
| `generate.py` | Prompt assembly and the one provider call, `call_model(prompt) -> str`. |
| `renderer.py` | Resolves ids to text and shells out to Typst. |
| `db.py` | SQLite store. The only module that writes SQL. |
| `ingest.py` | URL → posting. |
| `app.py`, `pages/`, `ui.py` | Streamlit interface. |
| `batch.py`, `intake.py` | CLI entry points. |
| `prompt.txt` | The prompt, with `{{JOB_TEXT}}`-style placeholders. Edit freely. |

Swapping model providers means rewriting `call_model` and nothing else. Prompt
building, validation and rendering never learn which model answered.

## Tuning

| Knob | Where | Default |
| --- | --- | --- |
| Model | `generate.py` | `claude-opus-5` |
| Max bullets per CV | `schema.py` | `8` |
| Minimum distinct projects | `schema.py` | `3` |
| Fetched-posting word cap | `ingest.py` | `350` |
| Selection rules, letter content | `prompt.txt` | — |

`MAX_BULLETS` and the figure in `prompt.txt` are separate — the prompt states
the limit in words, the schema enforces it. Change both.

## Caveats

- **Workable is untested.** The endpoint shape is confirmed, but no live
  posting has been through that adapter.
- **Garamond is not bundled.** `cover_letter.typ` falls back to Cochin, then
  Palatino, then Times New Roman.
- **One writer at a time.** SQLite with a single cached connection is fine for
  one person and one browser tab.
- **Deleting a job leaves its PDFs** under `out/<job_id>/`. Deliberate: an
  orphaned PDF is cheaper than a deleted one.

## Licence

No licence yet — all rights reserved by default. Add one before reusing this.
