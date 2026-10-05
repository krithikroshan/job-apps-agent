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
