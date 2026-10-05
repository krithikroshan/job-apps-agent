"""Relevance scoring.

Deliberately deterministic keyword scoring, not an LLM call. Three reasons:
it is free at ingestion volume, it is auditable (every score carries its
reasons, so you can see why a bad match ranked high and fix the weights), and
it is testable. Save the model calls for the cover letters, where judgement
actually matters.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from .models import Posting
from .profile import Profile


def score(posting: Posting, profile: Profile) -> Posting:
    title = posting.title.lower()
    body = f"{posting.title} {posting.description}".lower()
    points = 0
    reasons: list[str] = []

    # Hard exclusions first — cheap, and they kill most of the noise.
    for blocker in profile.title_blockers:
        if blocker in title:
            posting.score = -1
            posting.score_reasons = [f"excluded: title contains '{blocker.strip()}'"]
            return posting

    for blocker in profile.experience_blockers:
        if blocker in body:
            posting.score = -1
            posting.score_reasons = [f"excluded: requires '{blocker}'"]
            return posting

    # Title match — the dominant signal. Only the best one counts, so a title
    # stuffed with synonyms doesn't inflate.
    title_hits = [(w, t) for t, w in profile.target_titles.items() if t in title]

    # Domain vocabulary, capped so keyword-stuffed adverts don't dominate.
    domain_points = min(
        sum(w for term, w in profile.domain_terms.items() if _term_in(term, body)), 30)

    if title_hits:
        best_w, best_t = max(title_hits)
        points += best_w
        reasons.append(f"title '{best_t}' (+{best_w})")
    elif not (profile.domain_only_threshold
              and domain_points >= profile.domain_only_threshold):
        posting.score = -1
        posting.score_reasons = ["excluded: no target title match"]
        return posting
    else:
        reasons.append("no title match; kept on domain terms")

    if domain_points:
        points += domain_points
        reasons.append(f"domain terms (+{domain_points})")

    if profile.contract_bonus and posting.contract_type in ("contract", "temp", "contract_type"):
        points += profile.contract_bonus
        reasons.append(f"contract/temp ({profile.contract_bonus:+d})")

    # Salary: reward roles that clear the level the profile is aiming for,
    # without excluding unstated salaries, which are the majority.
    band = _salary_band(posting.salary_min, profile.salary_bands)
    if band:
        points += band[0]
        reasons.append(band[1])

    # Freshness. Agency roles fill in days; a three-week-old post is usually dead.
    if posting.posted:
        age = (date.today() - posting.posted).days
        if age <= 3:
            points += 10
            reasons.append("posted <= 3 days (+10)")
        elif age <= 7:
            points += 5
            reasons.append("posted <= 7 days (+5)")
        elif age > 28:
            points -= 10
            reasons.append("posted > 28 days (-10)")

    posting.score = points
    posting.score_reasons = reasons
    return posting


def _salary_band(salary_min: float | None,
                 bands: list[list[int]]) -> tuple[int, str] | None:
    """(points, reason) for the first band ``salary_min`` reaches, or None if
    the salary is unstated or the band is worth nothing."""
    if not salary_min:
        return None
    ordered = sorted(bands, key=lambda b: b[0], reverse=True)
    for i, (threshold, band_points) in enumerate(ordered):
        if salary_min < threshold:
            continue
        if not band_points:
            return None
        if threshold:
            return band_points, f"salary >= {_k(threshold)} ({band_points:+d})"
        if not i:  # a lone catch-all band: any stated salary
            return band_points, f"salary stated ({band_points:+d})"
        return band_points, f"salary < {_k(ordered[i - 1][0])} ({band_points:+d})"
    return None


def _k(amount: int) -> str:
    return f"{amount / 1000:g}k"


@lru_cache(maxsize=1024)
def _term_pattern(term: str) -> re.Pattern:
    # Anchored at the start of a word only, so "cima" misses "decimal" but
    # "graduate" still finds "graduates".
    return re.compile(r"(?<![a-z0-9])" + re.escape(term))


def _term_in(term: str, body: str) -> bool:
    return bool(_term_pattern(term).search(body))


def score_all(postings: list[Posting], profile: Profile) -> list[Posting]:
    scored = [score(p, profile) for p in postings]
    return [p for p in scored if p.score >= 0]
