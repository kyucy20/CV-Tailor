// ===========================================================================
// CV layout template — styled after the original LaTeX CV
// (TeX Gyre Heros / Helvetica, FontAwesome contact icons, ruled section
// headings, two-line entries with right-aligned dates and locations).
//
// renderer.py selects the content, writes it to a JSON file, and passes the
// file name via `--input data=<file>`. Everything below is layout: adjust it
// freely without touching Python.
//
// The ONLY text in this file is structural chrome — section headings, skill
// category labels, the word for a native language, date separators. Every CV
// sentence comes verbatim from CV.json via `data`.
// ===========================================================================

// Contact-line icons. Imported qualified as `fa` so nothing leaks into scope.
// The package is fetched once and cached; the glyphs need the Font Awesome 6
// font installed as well — see the note at the bottom of this file.
#import "@preview/fontawesome:0.5.0" as fa

#let data = if "data" in sys.inputs {
  json(sys.inputs.data)
} else {
  json("preview_data.json")
}
#let lang = data.lang

// ---------------------------------------------------------------------------
// KNOBS — the things you are most likely to want to change
// ---------------------------------------------------------------------------

// Section order. Drop a name to hide that section, move one to reorder.
#let SECTIONS = ("summary", "education", "experience", "projects", "skills", "languages")

#let surname-first = true      // "Elkoussy, Mostafa" as in the original
#let use-icons = false         // see the FontAwesome note at the bottom
#let justify-bullets = false   // the original is ragged-right, not justified

#let ink = rgb("#000000")      // headings, names, entry titles
#let body-ink = rgb("#1a1a1a") // bullet text
#let muted = rgb("#1a1a1a")    // dates, locations, roles
#let rule-ink = rgb("#c3c3c3") // the grey bar under each section heading

#let body-size = 10pt
#let name-size = 26pt          // the original's name is large and fully bold
#let mono-size = 9pt           // contact line, set in a monospace face
#let head-size = 13pt          // section headings
#let rule-weight = 2pt         // thick grey bar, not a hairline
#let sec-space = 1.5em         // space above a section heading
#let rule-gap = 0.42em         // gap between a heading label and its bar
#let entry-space = 0.95em      // space above an entry block
#let bullet-indent = 7mm       // the original indents bullets well in
#let bullet-size = 9.2pt       // bullet text, a little smaller than body
#let first-bullet-space = 0.72em  // gap between an entry heading and bullet 1

// Per-section spacing. These override the global settings above for just
// PROJEKTE and TECHNISCHE KENNTNISSE.
#let project-leading = 0.78em        // line spacing inside a project bullet
#let project-bullet-space = 0.72em   // gap between project bullets
#let project-entry-space = 1.15em    // gap above each project heading
#let skills-line-space = 0.62em      // gap between skill category lines

// ---------------------------------------------------------------------------
// CHROME — the only text not coming from CV.json
// ---------------------------------------------------------------------------

#let LABELS = (
  DE: (
    summary: "PROFIL",
    experience: "BERUFSERFAHRUNG",
    projects: "PROJEKTE",
    education: "AUSBILDUNG",
    skills: "TECHNISCHE KENNTNISSE",
    languages: "SPRACHEN",
    present: "heute",
    expected: "voraussichtlich",
    dash: " – ",
  ),
  EN: (
    summary: "PROFILE",
    experience: "EXPERIENCE",
    projects: "PROJECTS",
    education: "EDUCATION",
    skills: "TECHNICAL SKILLS",
    languages: "LANGUAGES",
    present: "present",
    expected: "expected",
    dash: " – ",
  ),
)

#let CATEGORIES = (
  DE: (
    languages: "Programmiersprachen",
    web: "Web & Full Stack",
    ai_data: "KI & Daten",
    tools: "Tools & Plattformen",
  ),
  EN: (
    languages: "Programming Languages",
    web: "Web & Full Stack",
    ai_data: "AI & Data",
    tools: "Tools & Platforms",
  ),
)

// CEFR levels print as-is; only "native" needs a word.
#let LEVELS = (
  DE: (native: "Muttersprache"),
  EN: (native: "Native"),
)

#let L = LABELS.at(lang)
#let CAT = CATEGORIES.at(lang)
#let LVL = LEVELS.at(lang)

// ---------------------------------------------------------------------------
// PAGE
// ---------------------------------------------------------------------------

#set page(paper: "a4", margin: (x: 1.5cm, top: 1.25cm, bottom: 1.15cm))
#set text(
  // The original was set in TeX Gyre Heros, a Helvetica clone. Put
  // "TeX Gyre Heros" first in this list if you install the font.
  font: ("TeX Gyre Heros", "Helvetica", "Arial"),
  size: body-size,
  fill: body-ink,
  lang: lower(lang),
  hyphenate: true,
)
// `spacing` lives on block rather than par: both old and new Typst accept it,
// whereas par(spacing:) needs 0.12+.
#set par(justify: justify-bullets, leading: 0.45em)
#set block(spacing: 0.5em)
#set list(marker: text(fill: ink)[•], indent: bullet-indent, body-indent: 0.5em, spacing: 0.42em)

// ---------------------------------------------------------------------------
// HELPERS
// ---------------------------------------------------------------------------

// "2025-08" -> "08/2025"; "present"/"expected" -> the localised word.
#let fmt-date(v) = {
  if v == "present" {
    L.present
  } else if v == "expected" {
    L.expected
  } else {
    let p = v.split("-")
    if p.len() == 2 { p.at(1) + "/" + p.at(0) } else { v }
  }
}

// A single-month span collapses to its year, matching the original's "2021".
#let date-range(start, end) = {
  if start == end {
    let p = start.split("-")
    if p.len() == 2 { p.at(0) } else { start }
  } else {
    fmt-date(start) + L.dash + fmt-date(end)
  }
}

#let level-label(lv) = LVL.at(lv, default: lv)

// Ruled section heading. `stack` is used rather than a paragraph break so the
// gap between the label and its rule is exact and not subject to par spacing.
#let section(title) = {
  block(above: sec-space, below: 0.75em, width: 100%)[
    #stack(
      spacing: rule-gap,
      text(size: head-size, weight: "bold", fill: ink)[#upper(title)],
      line(length: 100%, stroke: rule-weight + rule-ink),
    )
  ]
}

// Bold title left / date right, with an optional italic second line
// carrying the role or credential left and the location right.
#let entry(title, right, subtitle: none, subright: none, above: entry-space) = {
  block(above: above, below: 0.22em, width: 100%)[
    #grid(
      columns: (1fr, auto),
      column-gutter: 10pt,
      text(weight: "bold", fill: ink)[#title],
      text(fill: muted)[#right],
    )
    #if subtitle != none or subright != none {
      v(0.08em)
      grid(
        columns: (1fr, auto),
        column-gutter: 10pt,
        text(style: "italic", fill: muted)[#subtitle],
        text(style: "italic", fill: muted)[#subright],
      )
    }
  ]
}

// `above` is the gap between an entry's heading and its first bullet.
#let bullets(items, above: first-bullet-space) = {
  if items.len() > 0 {
    block(above: above, below: 0.1em, width: 100%)[
      #set text(size: bullet-size)
      #list(..items.map(t => [#t]))
    ]
  }
}

// ---------------------------------------------------------------------------
// HEADER
// ---------------------------------------------------------------------------

#let m = data.meta

// The whole name is bold, as in the original.
#let name-block(full) = {
  let parts = full.split(" ")
  let shown = if surname-first and parts.len() > 1 {
    parts.last() + ", " + parts.slice(0, parts.len() - 1).join(" ")
  } else {
    full
  }
  text(weight: "bold")[#shown]
}

// Contact fields in the original's order, each with its FontAwesome glyph.
// The icons only appear when `use-icons` is true — see the note at the
// bottom of this file for how to make the font available.
#let CONTACT_FIELDS = ("phone", "email", "github", "linkedin", "portfolio", "location")

#let ICONS = (
  phone: fa.fa-phone-alt(),
  email: fa.fa-envelope(),
  github: fa.fa-github(),
  linkedin: fa.fa-linkedin(),
  portfolio: fa.fa-globe(),
  location: fa.fa-map-marker-alt(),
)

#let contact-line(m) = {
  let parts = ()
  for k in CONTACT_FIELDS {
    let v = m.at(k, default: none)
    if v == none { continue }
    let icon = ICONS.at(k, default: none)
    if use-icons and icon != none {
      parts.push([#icon#h(0.35em)#v])
    } else {
      parts.push([#v])
    }
  }
  parts.join([#h(0.5em)|#h(0.5em)])
}

#align(center)[
  #text(size: name-size, fill: ink)[#name-block(m.name)]
  #v(0.5em)
  // The original sets the contact line in a monospace face (Fira Mono).
  #text(
    size: mono-size,
    fill: ink,
    // Menlo first: it ships with macOS, so no "unknown font family" warning.
    // Put "Fira Mono" (the original's face) first if you install it.
    font: ("Menlo", "DejaVu Sans Mono", "Courier New"),
  )[#contact-line(m)]
]
#v(0.5em)

// ---------------------------------------------------------------------------
// SECTIONS
// ---------------------------------------------------------------------------

#let sec-summary() = {
  let s = data.at("summary", default: none)
  if s != none {
    section(L.summary)
    block(width: 100%)[#s]
  }
}

#let sec-experience() = {
  if data.experience.len() > 0 {
    section(L.experience)
    for e in data.experience {
      entry(
        e.company,
        date-range(e.start, e.end),
        subtitle: e.role,
        subright: e.location,
      )
      bullets(e.bullets)
    }
  }
}

#let sec-projects() = {
  if data.projects.len() > 0 {
    section(L.projects)
    // Looser than the rest of the page — scoped to this section only.
    set par(leading: project-leading)
    set list(spacing: project-bullet-space)
    for p in data.projects {
      // "Name | Stack, Stack" — name bold, stack italic, as in the original.
      let title = [#p.name]
      if p.stack.len() > 0 {
        title = title + text(weight: "regular", style: "italic", fill: ink)[ | #p.stack.join(", ")]
      }
      // Passed explicitly: an argument on the block beats a `set block` rule.
      entry(title, p.year, above: project-entry-space)
      bullets(p.bullets)
    }
  }
}

#let sec-education() = {
  if data.education.len() > 0 {
    section(L.education)
    for ed in data.education {
      // The original puts credential and detail on one line: "American
      // Diploma – Notendurchschnitt: 3,97 / 4,0".
      let sub = ed.credential
      let detail = ed.at("detail", default: none)
      if detail != none { sub = sub + L.dash + detail }
      entry(
        ed.institution,
        date-range(ed.start, ed.end),
        subtitle: sub,
        subright: ed.location,
      )
    }
  }
}

#let sec-skills() = {
  if data.skills.len() > 0 {
    section(L.skills)
    // The original bolds the category but leaves the colon upright.
    for g in data.skills {
      let label = CAT.at(g.category, default: g.category)
      block(above: skills-line-space, below: skills-line-space, width: 100%)[
        #text(weight: "bold", fill: ink)[#label]: #g.names.join(", ")
      ]
    }
  }
}

#let sec-languages() = {
  if data.languages.len() > 0 {
    section(L.languages)
    // Language names are bold in the original, levels upright.
    let items = data.languages.map(l => [#text(weight: "bold", fill: ink)[#l.name]: #level-label(l.level)])
    block(above: 0.2em, width: 100%)[#items.join(h(2.2em))]
  }
}

#let RENDERERS = (
  summary: sec-summary,
  experience: sec-experience,
  projects: sec-projects,
  education: sec-education,
  skills: sec-skills,
  languages: sec-languages,
)

#for s in SECTIONS {
  let f = RENDERERS.at(s, default: none)
  if f != none { f() }
}

// ---------------------------------------------------------------------------
// FontAwesome contact icons
//
// The icons are wired up above: the `fa` import at the top of this file, the
// ICONS map next to `contact-line`, and the `use-icons` knob that switches
// them on. The Typst package is fetched and cached automatically, but the
// GLYPHS need the Font Awesome 6 font itself, which is a separate install.
// Without it every icon renders as a "?" tofu box.
//
// Two ways to provide the font — either works, pick one:
//
//   1. System-wide:  brew install --cask font-awesome
//
//   2. Project-local: drop these two files into a `fonts/` directory
//      next to this template (download from fontawesome.com, Free for
//      Desktop; the Free set is SIL OFL licensed):
//
//          fonts/Font Awesome 6 Free-Solid-900.otf
//          fonts/Font Awesome 6 Brands-Regular-400.otf
//
//      renderer.py passes `--font-path fonts` whenever that directory
//      exists, so nothing else needs changing.
//
// Then set `use-icons: true` in the KNOBS block at the top.
//
// NOTE: everything below a `//` is a comment. If the `//` markers are ever
// stripped, these lines become live Typst code and the compile fails.
// ---------------------------------------------------------------------------

