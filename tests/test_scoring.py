"""Scoring: the hard exclusions matter most — they decide what never gets seen."""

from datetime import date, timedelta

from conftest import make_posting

from jobs_agent.profile import Profile
from jobs_agent.scoring import score, score_all

# A representative configured profile — DEFAULT_PROFILE ships with no target
# titles (there's no sensible default for an arbitrary candidate), so the
# scoring behavior below is exercised against a stand-in instead.
P = Profile(
    name="",
    location="London",
    target_titles={"compliance analyst": 30, "paralegal": 26, "kyc analyst": 26},
    domain_terms={"fca": 10, "sanctions": 8, "due diligence": 8, "llb": 6, "graduate": 6},
    title_blockers=["senior", "head of", "manager"],
    experience_blockers=["5+ years"],
)


def test_title_blocker_excludes():
    p = score(make_posting(title="Senior Compliance Analyst"), P)
    assert p.score == -1
    assert "senior" in p.score_reasons[0]


def test_experience_blocker_excludes():
    p = score(make_posting(description="You will have 5+ years of experience."), P)
    assert p.score == -1
    assert "5+ years" in p.score_reasons[0]


def test_no_target_title_excludes():
    p = score(make_posting(title="Barista"), P)
    assert p.score == -1
    assert "no target title match" in p.score_reasons[0]


def test_only_the_best_title_match_counts():
    """A title stuffed with synonyms shouldn't outscore a clean one."""
    stuffed = score(make_posting(title="Compliance Analyst / Paralegal / KYC Analyst",
                                 description=""), P)
    clean = score(make_posting(title="Compliance Analyst", description=""), P)
    assert stuffed.score == clean.score == P.target_titles["compliance analyst"]


def test_domain_terms_are_capped_at_30():
    everything = " ".join(P.domain_terms)
    p = score(make_posting(description=everything), P)
    title_points = P.target_titles["compliance analyst"]
    assert p.score == title_points + 30


def test_freshness_and_staleness():
    fresh = score(make_posting(posted=date.today(), description=""), P)
    stale = score(make_posting(posted=date.today() - timedelta(days=40), description=""), P)
    assert fresh.score - stale.score == 20  # +10 fresh vs -10 stale


def test_salary_bands():
    base = score(make_posting(description=""), P).score
    assert score(make_posting(salary_min=40000, description=""), P).score == base + 10
    assert score(make_posting(salary_min=29000, description=""), P).score == base + 5
    assert score(make_posting(salary_min=18000, description=""), P).score == base - 8


def test_score_all_drops_the_excluded():
    kept = score_all([make_posting(), make_posting(title="Head of Compliance")], P)
    assert len(kept) == 1
    assert all(p.score >= 0 for p in kept)
