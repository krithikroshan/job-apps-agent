"""The fetch pipeline: pull, score, deduplicate, store.

The CLI's ``fetch`` command and the web UI's "Fetch new listings" button ran
separate copies of this sequence, which is how they drifted apart on the
default page size. Both now call :func:`fetch_and_store`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import KEYWORDS
from .profile import Profile, load_profile
from .scoring import score_all
from .sources import build_sources, gather_all
from .storage import Store


@dataclass
class FetchResult:
    raw: int          # postings returned by the boards
    kept: int         # survivors of scoring's hard exclusions
    new: int          # rows actually inserted
    duplicates: int   # suppressed as already known
    warnings: list[str]   # boards skipped for missing credentials

    @property
    def excluded(self) -> int:
        return self.raw - self.kept

    def as_dict(self) -> dict:
        return {
            "raw": self.raw, "kept": self.kept, "new": self.new,
            "duplicates": self.duplicates, "warnings": self.warnings,
        }


#: Most keyword x location searches one fetch runs against each board. Every
#: search is at least one API call per board, both boards rate-limit, and the
#: whole fetch runs inside one web request — roughly the volume a
#: single-location fetch of a full preset already made.
MAX_SEARCHES = 24


@dataclass
class SearchPlan:
    keywords: list[str]
    locations: list[str]
    warnings: list[str]


def search_plan(profile: Profile, keywords: list[str] | None = None) -> SearchPlan:
    """What a fetch searches: the profile's target titles, strongest first,
    in each of its locations — cut to :data:`MAX_SEARCHES` by dropping the
    weakest titles, since a location is a deliberate choice and a
    low-weight title is a long shot anyway."""
    if keywords:
        terms = list(keywords)
    elif profile.target_titles:
        terms = sorted(profile.target_titles, key=profile.target_titles.get, reverse=True)
    else:
        terms = list(KEYWORDS)
    locations = list(profile.locations)

    budget = max(1, MAX_SEARCHES // len(locations))
    warnings = []
    if len(terms) > budget:
        warnings.append(
            f"searched the top {budget} of {len(terms)} target titles in "
            f"{len(locations)} location(s) — remove a location or some titles "
            f"to search the rest")
        terms = terms[:budget]
    return SearchPlan(keywords=terms, locations=locations, warnings=warnings)


async def fetch_and_store(store: Store, *, per_keyword: int = 200,
                          keywords: list[str] | None = None) -> FetchResult:
    """Fetch every keyword from every configured board and stage the results.

    The keywords searched are the profile's target titles — what actually
    gets fetched should track what scoring will accept, or a profile aimed at
    a different field just gets zero matches back. ``KEYWORDS`` is only a
    fallback for a profile with no target titles set at all. Each is searched
    in every one of the profile's locations; see :func:`search_plan`.

    Raises :class:`~jobs_agent.sources.NoSourcesConfigured` when no board has
    credentials — callers decide whether that's an exit or an HTTP 400.
    """
    sources, warnings = build_sources()
    profile = load_profile(store)

    plan = search_plan(profile, keywords)
    raw = await gather_all(sources, plan.keywords, plan.locations,
                           radius_miles=profile.radius_miles, per_keyword=per_keyword)
    kept = score_all(raw, profile)
    new, dup = store.upsert(kept)

    return FetchResult(raw=len(raw), kept=len(kept), new=new,
                       duplicates=dup, warnings=warnings + plan.warnings)


def fetch_and_store_sync(store: Store, *, per_keyword: int = 200,
                         keywords: list[str] | None = None) -> FetchResult:
    """Blocking wrapper, for the synchronous HTTP handler."""
    return asyncio.run(
        fetch_and_store(store, per_keyword=per_keyword, keywords=keywords)
    )
