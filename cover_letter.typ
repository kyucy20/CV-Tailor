// ===========================================================================
// Cover letter layout — matched to "Mostafa Elkoussy cover letter.docx".
//
// Every value in the KNOBS block below was read out of that file's XML rather
// than eyeballed, so the numbers are the Word document's own:
//
//   page      12240 x 15840 twips  -> US Letter, 1in margins all round
//   borders   #92D050, w:sz 4 (0.5pt), 24pt from the page edge, LEFT+RIGHT only
//   font      theme minorFont      -> Garamond
//   name      w:sz 96              -> 48pt, colour #4EA72E, left aligned
//   address   docDefaults w:sz 22  -> 11pt
//   body      w:sz 24              -> 12pt
//   spacing   w:after 200          -> 10pt after each paragraph
//             w:line 288 auto      -> 1.2 line spacing
//
// renderer.py writes the payload to JSON and passes it via `--input data=`.
// The only text in this file is the date chrome; every sentence of the letter
// comes from the model's `cover_letter` field, printed verbatim.
// ===========================================================================

#let data = if "data" in sys.inputs {
  json(sys.inputs.data)
} else {
  json("preview_letter.json")
}
#let lang = data.lang

// ---------------------------------------------------------------------------
// KNOBS
// ---------------------------------------------------------------------------

#let name-ink = rgb("#4EA72E")    // the green of the big name
#let border-ink = rgb("#92D050")  // the lighter green of the side rules
#let ink = rgb("#000000")

#let name-size = 48pt             // the docx really is this large
#let address-size = 11pt          // recipient block and subject line
#let body-size = 12pt             // salutation, paragraphs, sign-off

#let border-inset = 24pt          // distance from the page edge to each rule
#let border-weight = 0.5pt
#let show-borders = true          // the green left/right rules

#let para-space = 10pt            // w:after 200 twips
#let line-spacing = 0.8em         // approximates Word's 1.2 line spacing
#let name-space = 1.1em           // gap under the name
#let address-space = 1.0em        // gap under the subject line

// The docx has no contact line and no date — the letterhead is the name and
// nothing else. Flip either on if you want them; both are laid out to match.
#let show-contact-line = false
#let show-date = false

// The docx writes the name first-name-first, unlike the CV's "Elkoussy,
// Mostafa". Set true to match the CV instead.
#let surname-first = false

// ---------------------------------------------------------------------------
// CHROME — the only text not coming from the model or CV.json
// ---------------------------------------------------------------------------

#let MONTHS = (
  DE: ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
       "August", "September", "Oktober", "November", "Dezember"),
  EN: ("January", "February", "March", "April", "May", "June", "July",
       "August", "September", "October", "November", "December"),
)

#let today-line() = {
  let d = datetime.today()
  let month = MONTHS.at(lang).at(d.month() - 1)
  if lang == "DE" {
    str(d.day()) + ". " + month + " " + str(d.year())
  } else {
    month + " " + str(d.day()) + ", " + str(d.year())
  }
}

// ---------------------------------------------------------------------------
// PAGE
// ---------------------------------------------------------------------------

#let side-rule = line(
  angle: 90deg,
  length: 100%,
  stroke: border-weight + border-ink,
)

#set page(
  paper: "us-letter",
  margin: 1in,
  background: if show-borders {
    place(top + left, dx: border-inset, side-rule)
    place(top + right, dx: -border-inset, side-rule)
  },
)

#set text(
  // Garamond is the docx's theme font. It is not installed here, so the
  // fallbacks carry the look: Cochin is the closest old-style face macOS
  // ships. Install EB Garamond and it will be picked up automatically.
  font: ("Garamond", "EB Garamond", "Adobe Garamond Pro", "Cochin", "Palatino",
         "Times New Roman"),
  size: body-size,
  fill: ink,
  lang: lower(lang),
  hyphenate: true,
)
#set par(justify: false, leading: line-spacing)   // the docx is ragged-right
#set block(spacing: para-space)

// ---------------------------------------------------------------------------
// LETTERHEAD
// ---------------------------------------------------------------------------

#let m = data.meta

#let shown-name = {
  let parts = m.name.split(" ")
  if surname-first and parts.len() > 1 {
    parts.last() + ", " + parts.slice(0, parts.len() - 1).join(" ")
  } else {
    m.name
  }
}

#block(below: name-space)[
  #text(size: name-size, fill: name-ink)[#shown-name]
]

#if show-contact-line {
  let fields = ("phone", "email", "github", "linkedin", "portfolio", "location")
  let parts = ()
  for k in fields {
    let v = m.at(k, default: none)
    if v != none { parts.push([#v]) }
  }
  block(below: address-space)[
    #text(size: address-size)[#parts.join([#h(0.5em)|#h(0.5em)])]
  ]
}

// Recipient block and subject line, both 11pt as in the docx. Either is
// omitted entirely when the payload does not carry it.
#let recipient = data.at("recipient", default: none)
#if recipient != none {
  block(below: 0.35em)[#text(size: address-size)[#recipient]]
}

#let subject = data.at("subject", default: none)
#if subject != none {
  block(below: address-space)[#text(size: address-size,weight: "bold")[#subject]]
}

#if show-date {
  block(below: address-space)[#text(size: address-size)[#today-line()]]
}

// ---------------------------------------------------------------------------
// BODY — every paragraph verbatim from the model's cover_letter field
// ---------------------------------------------------------------------------

#for para in data.body {
  block(width: 100%)[#para]
}
