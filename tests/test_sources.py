"""Job-board adapters: the location and radius actually reach each board."""

import asyncio

import httpx

from jobs_agent.sources import AdzunaSource, ReedSource, gather_all


def run(coro):
    return asyncio.run(coro)


def capture(payload):
    """An AsyncClient whose every response is ``payload``, recording requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def test_reed_sends_the_location_and_radius():
    client, seen = capture({"results": [], "totalResults": 0})
    run(ReedSource("k").fetch(client, "graduate accountant", 10,
                              location="Manchester", radius_miles=25))
    params = seen[0].url.params
    assert params["locationName"] == "Manchester"
    assert params["distanceFromLocation"] == "25"


def test_reed_searches_anywhere_when_the_location_is_uk():
    client, seen = capture({"results": [], "totalResults": 0})
    run(ReedSource("k").fetch(client, "graduate accountant", 10,
                              location="UK", radius_miles=25))
    params = seen[0].url.params
    assert "locationName" not in params
    assert "distanceFromLocation" not in params


def test_adzuna_sends_the_location_and_radius_in_km():
    client, seen = capture({"results": []})
    run(AdzunaSource("id", "key").fetch(client, "audit trainee", 10,
                                        location="Leeds", radius_miles=10))
    params = seen[0].url.params
    assert params["where"] == "Leeds"
    assert params["distance"] == "16"  # 10 miles


def test_adzuna_searches_anywhere_when_the_location_is_uk():
    client, seen = capture({"results": []})
    run(AdzunaSource("id", "key").fetch(client, "audit trainee", 10,
                                        location="UK", radius_miles=10))
    params = seen[0].url.params
    assert "where" not in params
    assert "distance" not in params


class FakeSource:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def fetch(self, client, keyword, max_results=300, *, location, radius_miles):
        self.calls.append((keyword, location, radius_miles))
        return []


def test_gather_all_fans_out_over_every_keyword_and_location():
    src = FakeSource()
    run(gather_all([src], ["a", "b"], ["London", "Leeds"], radius_miles=20))
    assert sorted(src.calls) == [
        ("a", "Leeds", 20), ("a", "London", 20),
        ("b", "Leeds", 20), ("b", "London", 20),
    ]


def test_adzuna_sends_the_category_tag_for_a_known_category():
    client, seen = capture({"results": []})
    run(AdzunaSource("id", "key", category="accounting").fetch(
        client, "audit trainee", 10, location="UK", radius_miles=10))
    assert seen[0].url.params["category"] == "accounting-finance-jobs"


def test_adzuna_sends_no_category_by_default():
    client, seen = capture({"results": []})
    run(AdzunaSource("id", "key").fetch(client, "audit trainee", 10,
                                        location="UK", radius_miles=10))
    assert "category" not in seen[0].url.params


def test_every_profile_category_has_an_adzuna_tag():
    from jobs_agent.profile import JOB_CATEGORIES
    from jobs_agent.sources.adzuna import ADZUNA_CATEGORIES

    assert set(ADZUNA_CATEGORIES) == set(JOB_CATEGORIES) - {""}


def test_adzuna_maps_its_created_timestamp_to_a_date():
    from datetime import date

    p = AdzunaSource._to_posting({"id": 1, "created": "2026-09-30T08:15:00Z"})
    assert p.posted == date(2026, 9, 30)


def test_parse_date_reads_a_prefix_of_a_longer_timestamp():
    from datetime import date

    from jobs_agent.sources.base import parse_date

    assert parse_date("2026-09-15T12:55:35.3870000", "%Y-%m-%d") == date(2026, 9, 15)
    assert parse_date("15/09/2026", "%d/%m/%Y") == date(2026, 9, 15)
    assert parse_date("garbage", "%Y-%m-%d") is None
