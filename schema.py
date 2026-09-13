from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal,Optional
import re
DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

SKILL_CATEGORIES = ("languages", "web", "ai_data", "tools")
LANGS = ("DE", "EN")
ANGLES = ("technical", "impact")

# Localised fields are dicts keyed by lowercase language code, matching the
# existing Experience.role / Project.name convention.
LOCALE_KEYS = {"de", "en"}

Localized = dict[str, str]


def check_localized(v: Localized, field: str) -> Localized:
    """A localised field must be non-empty and keyed only by 'de' / 'en'."""
    if not v:
        raise ValueError(f"{field}: must have at least one of {sorted(LOCALE_KEYS)}")
    bad = sorted(set(v) - LOCALE_KEYS)
    if bad:
        raise ValueError(f"{field}: unexpected language keys {bad}, expected {sorted(LOCALE_KEYS)}")
    return v


def localized(v: Localized, lang: str, field: str = "field") -> str:
    """Read a localised field in `lang` ('DE'/'EN'). Raises if absent —
    consistent with bullets, a render never falls back across languages."""
    key = lang.lower()
    if key not in v:
        raise LookupError(
            f"{field}: no {lang!r} text (has {sorted(v)}); "
            f"refusing to fall back across languages."
        )
    return v[key]

class User(BaseModel):
    angle : Literal["technical","impact"]
    Lang : Literal["DE","EN"]
    text : str =Field(min_length=20)

class Meta(BaseModel):
    name: str
    email: str
    phone: str
    location: Localized
    github: str
    linkedin: Optional[str] = None
    portfolio: Optional[str] = None

    @field_validator("location")
    @classmethod
    def check_locales(cls, v, info) -> Localized:
        return check_localized(v, info.field_name)

    def text(self, lang: str) -> dict[str, str]:
        """Contact fields resolved into `lang`, with unset ones dropped."""
        out = {k: v for k, v in self.model_dump().items() if v is not None}
        out["location"] = localized(self.location, lang, "meta.location")
        return out

class Education(BaseModel):
    institution: Localized
    credential: Localized
    location: Localized
    start: str
    end: str
    detail: Optional[Localized] = None

    @field_validator("start", "end")
    @classmethod
    def check_date(cls, v: str) -> str:
        if v not in ("present", "expected") and not DATE.match(v):
            raise ValueError(f"expected YYYY-MM, 'present' or 'expected', got {v!r}")
        return v

    @field_validator("institution", "credential", "location", "detail")
    @classmethod
    def check_locales(cls, v, info) -> Optional[Localized]:
        if v is None:
            return v
        return check_localized(v, info.field_name)

    def text(self, lang: str) -> dict[str, str | None]:
        """This entry's localised fields resolved into `lang`."""
        return {
            "institution": localized(self.institution, lang, "education.institution"),
            "credential": localized(self.credential, lang, "education.credential"),
            "location": localized(self.location, lang, "education.location"),
            "detail": (
                localized(self.detail, lang, "education.detail")
                if self.detail is not None
                else None
            ),
        }

class Bullet(BaseModel):
    id: str
    variants : list[User]
    tags: list[str]
    has_metric : bool = False
    strength : int = 3

    def variant(self, lang: str, angle: str) -> User:
        """Pick the variant for (lang, angle).

        Falls back to the other angle in the SAME language. Never falls back
        across languages — a missing language raises instead, so a DE render
        can never silently emit English text.
        """
        for v in self.variants:
            if v.Lang == lang and v.angle == angle:
                return v
        other = "impact" if angle == "technical" else "technical"
        for v in self.variants:
            if v.Lang == lang and v.angle == other:
                return v
        have = sorted({v.Lang for v in self.variants})
        raise LookupError(
            f"bullet {self.id!r}: no variant in language {lang!r} "
            f"(tried angle {angle!r}, then {other!r}); variants present for {have}. "
            f"Refusing to fall back across languages."
        )

class Experience(BaseModel):
    id: str
    company: str
    role: Localized
    location: Localized
    start: str
    end: str
    context: Optional[str] = None
    bullets: list[Bullet]

    @field_validator("start", "end")
    @classmethod
    def check_date(cls, v: str) -> str:
        if v != "present" and not DATE.match(v):
            raise ValueError(f"expected YYYY-MM or 'present', got {v!r}")
        return v

    @field_validator("role", "location")
    @classmethod
    def check_locales(cls, v, info) -> Localized:
        return check_localized(v, info.field_name)

class Project(BaseModel):
    id: str
    name: Localized
    year: str
    stack: list[str]
    context: Optional[str] = None
    bullets: list[Bullet]

    @field_validator("name")
    @classmethod
    def check_locales(cls, v, info) -> Localized:
        return check_localized(v, info.field_name)
class Skill(BaseModel):
    name: str
    category: Literal["languages", "web", "ai_data", "tools"]
    level: Literal["primary", "working", "familiar"]
    aliases: list[str] = []
    years: Optional[int] = None


class Language(BaseModel):
    name: Localized
    level: Literal["A1", "A2", "B1", "B2", "C1", "C2", "native"]

    @field_validator("name")
    @classmethod
    def check_locales(cls, v, info) -> Localized:
        return check_localized(v, info.field_name)

    def text(self, lang: str) -> dict[str, str]:
        return {"name": localized(self.name, lang, "language.name"), "level": self.level}


class Summary(BaseModel):
    id: str
    lang: Literal["DE", "EN"]
    tags: list[str]
    text: str = Field(min_length=40)


class Profile(BaseModel):
    version: str = "1.0"
    meta: Meta
    summaries: list[Summary]
    experience: list[Experience]
    projects: list[Project]
    skills: list[Skill]
    education: list[Education]
    languages: list[Language]

    def all_ids(self) -> set[str]:
        ids = {s.id for s in self.summaries}
        for group in (self.experience, self.projects):
            for item in group:
                ids.add(item.id)
                ids.update(b.id for b in item.bullets)
        return ids

    def _locate(self, bullet_id: str) -> tuple["Bullet", Experience | Project]:
        for group in (self.experience, self.projects):
            for item in group:
                for b in item.bullets:
                    if b.id == bullet_id:
                        return b, item
        raise KeyError(
            f"bullet id {bullet_id!r} not found in profile "
            f"(known bullet ids: {sorted(self.bullet_ids())})"
        )

    def bullet_ids(self) -> set[str]:
        return {
            b.id
            for group in (self.experience, self.projects)
            for item in group
            for b in item.bullets
        }

    def find_bullet(self, bullet_id: str) -> "Bullet":
        """Return the Bullet with this id, or raise KeyError naming the id."""
        return self._locate(bullet_id)[0]

    def parent_of(self, bullet_id: str) -> Experience | Project:
        """Return the Experience or Project the bullet belongs to."""
        return self._locate(bullet_id)[1]

    def find_summary(self, summary_id: str) -> Summary:
        for s in self.summaries:
            if s.id == summary_id:
                return s
        raise KeyError(
            f"summary id {summary_id!r} not found in profile "
            f"(known summary ids: {sorted(s.id for s in self.summaries)})"
        )

    def skills_by_category(self) -> dict[str, list[Skill]]:
        """Skills grouped by category, in canonical category order.
        Order within a category follows CV.json. Empty categories are omitted.
        """
        groups: dict[str, list[Skill]] = {}
        for cat in SKILL_CATEGORIES:
            found = [s for s in self.skills if s.category == cat]
            if found:
                groups[cat] = found
        return groups

    @model_validator(mode="after")
    def ids_unique(self):
        seen, dupes = set(), set()
        for group in (self.experience, self.projects):
            for item in group:
                for b in item.bullets:
                    if b.id in seen:
                        dupes.add(b.id)
                    seen.add(b.id)
        if dupes:
            raise ValueError(f"duplicate bullet ids: {sorted(dupes)}")
        return self