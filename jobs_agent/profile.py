"""Candidate scoring profile.

Single source of truth for what counts as a good match. Scoring reads from
here; nothing else should carry candidate facts.

The profile lives in the database (``documents`` row ``scoring_profile``, as
JSON) so it can be tuned from the Profile page without editing code.
:data:`DEFAULT_PROFILE` is the seed used when that row is absent, and what
"Reset to defaults" restores.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing storage at runtime — it imports config
    from .storage import Store


class ProfileError(ValueError):
    """A profile edit couldn't be parsed. The message names the bad line."""


@dataclass
class Profile:
    name: str
    location: str

    # Titles we actively want, strongest signal first. Matched case-insensitively
    # as substrings against the job title.
    target_titles: dict[str, int] = field(default_factory=dict)

    # Domain vocabulary. Matched against title + description. Lower weight than
    # title matches because descriptions are noisy and keyword-stuffed.
    domain_terms: dict[str, int] = field(default_factory=dict)

    # Hard exclusions: if any appears in the TITLE, the posting is dropped.
    # These are seniority markers, not topics.
    title_blockers: list[str] = field(default_factory=list)

    # Experience thresholds that disqualify. Matched against description.
    experience_blockers: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False)

    @classmethod
    def from_json(cls, raw: str) -> "Profile":
        data = json.loads(raw)
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ProfileError(f"unknown profile fields: {', '.join(sorted(unknown))}")
        return cls(**data)


DEFAULT_PROFILE = Profile(
    name="",
    location="London",
    # Empty: there's no sensible default target title for an arbitrary
    # candidate, and scoring hard-excludes anything that doesn't match one
    # (see scoring.py), so this is set on the Profile page before first use.
    target_titles={},
    domain_terms={
        "financial regulation": 12,
        "fca": 10,
        "prudential": 8,
        "sanctions": 8,
        "onboarding": 6,
        "due diligence": 8,
        "corporate finance": 8,
        "capital markets": 6,
        "insolvency": 6,
        "litigation": 5,
        "contract review": 6,
        "legal research": 8,
        "drafting": 6,
        "banking": 5,
        "asset management": 5,
        "fintech": 5,
        "bar": 4,
        "llb": 6,
        "law graduate": 10,
        "no experience": 8,
        "entry level": 8,
        "graduate": 6,
    },
    title_blockers=[
        "head of",
        "director",
        "vp ",
        "vice president",
        "senior",
        "lead ",
        "principal",
        "manager",
        "partner",
        "chief",
        "counsel",  # "General Counsel", "Senior Counsel" — qualified roles
    ],
    experience_blockers=[
        "3+ years",
        "4+ years",
        "5+ years",
        "6+ years",
        "7+ years",
        "10+ years",
        "three years",
        "four years",
        "five years",
        "minimum of 3 years",
        "minimum of 5 years",
        "qualified solicitor",
        "must be sra",
        "nq solicitor",
    ],
)


# -- persistence ----------------------------------------------------------

def load_profile(store: "Store") -> Profile:
    """The stored profile, or :data:`DEFAULT_PROFILE` if none is saved yet.

    A corrupted row falls back to the default rather than taking the whole
    queue down; scoring something with the default beats scoring nothing.
    """
    from .storage import DOC_SCORING_PROFILE

    raw = store.get_document(DOC_SCORING_PROFILE)
    if not raw.strip():
        return DEFAULT_PROFILE
    try:
        return Profile.from_json(raw)
    except (json.JSONDecodeError, TypeError, ProfileError):
        return DEFAULT_PROFILE


def save_profile(store: "Store", profile: Profile) -> None:
    from .storage import DOC_SCORING_PROFILE

    store.set_document(DOC_SCORING_PROFILE, profile.to_json())


# -- the text formats the Profile page edits ------------------------------
#
# Weighted sections are "term = weight", one per line; blocker lists are one
# term per line. Friendlier to edit than raw JSON, and a mistyped line can be
# reported precisely instead of failing the whole document.

def format_weights(weights: dict[str, int]) -> str:
    return "\n".join(f"{term} = {weight}" for term, weight in weights.items())


def parse_weights(text: str, *, what: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        term, sep, weight = line.rpartition("=")
        if not sep:
            raise ProfileError(
                f"{what}, line {lineno}: expected 'term = weight', got {line!r}"
            )
        term = term.strip().lower()
        if not term:
            raise ProfileError(f"{what}, line {lineno}: missing the term")
        try:
            out[term] = int(weight.strip())
        except ValueError:
            raise ProfileError(
                f"{what}, line {lineno}: {weight.strip()!r} is not a whole number"
            ) from None
    return out


def format_lines(terms: list[str]) -> str:
    return "\n".join(terms)


def parse_lines(text: str) -> list[str]:
    """One term per line. Trailing spaces are significant in blockers like
    ``"vp "`` and ``"lead "``, so only the line ending is stripped."""
    out = []
    for raw in text.splitlines():
        term = raw.rstrip("\r\n").lower()
        if term.strip() and not term.lstrip().startswith("#"):
            out.append(term)
    return out
