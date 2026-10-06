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
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing storage at runtime — it imports config
    from .storage import Store


class ProfileError(ValueError):
    """A profile edit couldn't be parsed. The message names the bad line."""


#: Searched as "anywhere in the UK" rather than as a place name.
LOCATION_ANYWHERE = "UK"
MAX_LOCATIONS = 5
MAX_RADIUS_MILES = 100
#: Domain terms are capped at 30 points in scoring, so a higher threshold
#: could never be met.
MAX_DOMAIN_ONLY_THRESHOLD = 30
MAX_SALARY_THRESHOLD = 1_000_000
#: Band points sit alongside title weights (~30) and the domain cap (30).
MAX_SALARY_POINTS = 100

#: Board-neutral job sectors a search can be narrowed to, slug -> label.
#: "" searches every sector. Each adapter that supports sectors maps these
#: slugs to its own tags (see ``sources/adzuna.py``); one that doesn't just
#: ignores the setting, so a slug is never a reason to skip a board.
JOB_CATEGORIES: dict[str, str] = {
    "": "Any",
    "accounting": "Accounting & finance",
    "legal": "Legal",
    "it": "IT",
    "engineering": "Engineering",
    "marketing": "Marketing, PR & advertising",
    "hr": "HR & recruitment",
    "consultancy": "Consultancy",
    "graduate": "Graduate",
}


def _default_salary_bands() -> list[list[int]]:
    return [[33_400, 10], [28_000, 5], [22_000, 0], [0, -8]]


@dataclass
class Profile:
    name: str

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

    # Where to search: place names passed to each job board, or
    # LOCATION_ANYWHERE for the whole UK. Every keyword is searched in every
    # location, so each extra location multiplies the job-board calls.
    locations: list[str] = field(default_factory=lambda: ["London"])
    radius_miles: int = 15

    # A JOB_CATEGORIES slug narrowing the boards that support sectors, or ""
    # for every sector. Titles alone match too broadly on Adzuna ("trainee"
    # finds trainee electricians), which a sector filter cuts out cheaply.
    job_category: str = ""

    # [threshold, points] pairs, highest threshold first. A posting's
    # minimum salary earns the points of the first band it reaches; a 0
    # threshold catches everything below the others. Unstated salaries (the
    # majority) score nothing either way.
    salary_bands: list[list[int]] = field(default_factory=_default_salary_bands)

    # Points for contract/temp roles: they hire fast and rarely need
    # sponsorship. 0 to treat them like any other role.
    contract_bonus: int = 8

    # 0: a posting must match a target title. Above 0: one that matches no
    # title is still kept if its domain terms score at least this much —
    # for fields like graduate schemes, whose titles vary too much to list.
    domain_only_threshold: int = 0

    # The preset this profile was last loaded from, for display only.
    preset: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False)

    @classmethod
    def from_json(cls, raw: str) -> "Profile":
        data = json.loads(raw)
        # Profiles saved before multi-location search carry one "location".
        legacy_location = data.pop("location", None)
        if legacy_location and "locations" not in data:
            data["locations"] = [legacy_location]
        if "locations" in data and not data["locations"]:
            del data["locations"]  # searching nowhere isn't a setting; use the default
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ProfileError(f"unknown profile fields: {', '.join(sorted(unknown))}")
        return cls(**data)


DEFAULT_PROFILE = Profile(
    name="",
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


def format_locations(locations: list[str]) -> str:
    return ", ".join(locations)


def parse_locations(text: str) -> list[str]:
    """Comma- or newline-separated place names, deduplicated ignoring case.
    Casing is kept as typed: it's what the job boards are sent."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,\n]", text or ""):
        place = " ".join(raw.split())
        if not place or place.lower() in seen:
            continue
        if len(place) > 60:
            raise ProfileError(f"Locations: {place[:20]!r}… is too long for a place name")
        seen.add(place.lower())
        out.append(place)
    if not out:
        raise ProfileError(
            f"Locations: add at least one place, or {LOCATION_ANYWHERE!r} for anywhere")
    if len(out) > MAX_LOCATIONS:
        raise ProfileError(
            f"Locations: at most {MAX_LOCATIONS} — each one multiplies the searches a fetch runs")
    return out


def parse_job_category(text: str) -> str:
    """A :data:`JOB_CATEGORIES` slug, or "" for any sector."""
    slug = (text or "").strip().lower()
    if slug not in JOB_CATEGORIES:
        raise ProfileError(
            f"Job category: {slug!r} isn't one of "
            f"{', '.join(repr(k) for k in JOB_CATEGORIES if k)}")
    return slug


def parse_int(text: str, *, what: str, lo: int, hi: int) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        raise ProfileError(f"{what}: {str(text).strip()!r} is not a whole number") from None
    if not lo <= value <= hi:
        raise ProfileError(f"{what}: must be between {lo} and {hi}")
    return value


_MONEY = re.compile(r"^£?\s*(\d[\d,]*(?:\.\d+)?)\s*(k?)$", re.IGNORECASE)


def format_salary_bands(bands: list[list[int]]) -> str:
    return "\n".join(f"{threshold} = {points}" for threshold, points in bands)


def parse_salary_bands(text: str) -> list[list[int]]:
    """``threshold = points`` per line; thresholds may be written ``£30,000``
    or ``30k``. Returned highest threshold first, the order scoring reads."""
    bands: dict[int, int] = {}
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        raw_threshold, sep, raw_points = line.rpartition("=")
        if not sep:
            raise ProfileError(
                f"Salary bands, line {lineno}: expected 'amount = points', got {line!r}")
        points = parse_int(raw_points, what=f"Salary bands, line {lineno}",
                           lo=-MAX_SALARY_POINTS, hi=MAX_SALARY_POINTS)
        bands[_parse_amount(raw_threshold, lineno)] = points
    return [[t, bands[t]] for t in sorted(bands, reverse=True)]


def _parse_amount(raw: str, lineno: int) -> int:
    """A salary threshold: ``30000``, ``£30,000``, or ``30k``."""
    match = _MONEY.match(raw.strip())
    if not match:
        raise ProfileError(f"Salary bands, line {lineno}: {raw.strip()!r} is not an amount")
    amount = float(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 1000
    if amount > MAX_SALARY_THRESHOLD:
        raise ProfileError(
            f"Salary bands, line {lineno}: amounts go up to £{MAX_SALARY_THRESHOLD:,}")
    return int(amount)
