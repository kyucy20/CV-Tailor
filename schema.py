from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal,Optional
import re
DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

class User(BaseModel):
    angle : Literal["technical","impact"]
    Lang : Literal["DE","EN"]
    text : str =Field(min_length=20)

class Meta(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    github: str
    linkedin: Optional[str] = None
    portfolio: Optional[str] = None

class Education(BaseModel):
    institution: str
    credential: str
    location: str
    start: str
    end: str
    detail: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def check_date(cls, v: str) -> str:
        if v not in ("present", "expected") and not DATE.match(v):
            raise ValueError(f"expected YYYY-MM, 'present' or 'expected', got {v!r}")
        return v

class Bullet(BaseModel):
    id: str
    variants : list[User]
    tags: list[str]
    has_metric : bool = False
    strength : int = 3

class Experience(BaseModel):
    id: str
    company: str
    role: dict[str, str]
    location: str
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

class Project(BaseModel):
    id: str
    name: dict[str, str]
    year: str
    stack: list[str]
    context: Optional[str] = None
    bullets: list[Bullet]
class Skill(BaseModel):
    name: str
    category: Literal["languages", "web", "ai_data", "tools"]
    level: Literal["primary", "working", "familiar"]
    aliases: list[str] = []
    years: Optional[int] = None


class Language(BaseModel):
    name: str
    level: Literal["A1", "A2", "B1", "B2", "C1", "C2", "native"]


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