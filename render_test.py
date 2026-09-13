"""Render the same selection twice — EN/impact and DE/technical — to compare.

    python render_test.py

Writes out/cv_en_impact.pdf and out/cv_de_technical.pdf.
"""

from pathlib import Path

from renderer import BREW_HINT, build_payload, load_profile, render

OUT = Path(__file__).resolve().parent / "out"

# One id list, rendered both ways. Order here is the order on the page.
BULLETS = [
    "exp_1_b1",
    "exp_1_b2",
    "exp_1_b3",
    # Three projects, both bullets each.
    "prj_rag_b1",
    "prj_rag_b2",
    "prj_chatbot_b1",
    "prj_chatbot_b2",
    "prj_bottle_robot_b1",
    "prj_bottle_robot_b2",
]

RUNS = [
    ("cv_en_impact.pdf", "sum_en_ai", "EN", "impact"),
    ("cv_de_technical.pdf", "sum_de_ai", "DE", "technical"),
]


def main() -> None:
    profile = load_profile()
    OUT.mkdir(exist_ok=True)

    for filename, summary_id, lang, angle in RUNS:
        target = OUT / filename
        # Resolve first: content errors should surface even without typst.
        payload = build_payload(profile, summary_id, BULLETS, lang, angle)
        n = sum(len(e["bullets"]) for e in payload["experience"]) + sum(
            len(p["bullets"]) for p in payload["projects"]
        )
        print(f"{lang}/{angle}: {n} bullets, {len(payload['experience'])} experience, "
              f"{len(payload['projects'])} projects -> {target.name}")
        try:
            render(profile, summary_id, BULLETS, lang, angle, str(target))
        except RuntimeError as e:
            if BREW_HINT in str(e):
                print(f"\n{e}")
                return
            raise

    print(f"\nWrote both PDFs to {OUT}")


if __name__ == "__main__":
    main()
