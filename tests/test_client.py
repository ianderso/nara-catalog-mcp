"""Client behaviour: auth header, error mapping, unwrapping and caching.

The cache is load-bearing rather than a nicety: the API key is capped at
10,000 calls a month, so a key collision serves a wrong answer and a key miss
spends quota that cannot be got back.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from nara_catalog_mcp.client import (
    API_BASE,
    BUDGET_FILE,
    NaraApiError,
    NaraClient,
    current_month,
    cursor,
    unwrap,
    unwrap_any,
)

from .conftest import cache_entries, contribution_payload, search_payload


@respx.mock
async def test_search_sends_api_key_header(client, record):
    """The key travels as x-api-key, which is what the Catalog requires."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="54765873")
    assert route.calls.last.request.headers["x-api-key"] == "test-key"


@respx.mock
async def test_search_caches_and_does_not_recall(client, record):
    """A repeated query is served from disk, leaving the monthly quota alone."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="54765873")
    await client.search(naId="54765873")
    assert client.live_calls == 1
    assert client.cache_hits == 1


@respx.mock
async def test_cache_key_distinguishes_queries(client, record):
    """Different parameters must not collide in the cache."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="1")
    await client.search(naId="2")
    assert client.live_calls == 2


@respx.mock
async def test_http_error_becomes_nara_api_error(client):
    """A 403 (bad or exhausted key) surfaces with its status intact."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )
    with pytest.raises(NaraApiError) as exc:
        await client.search(naId="1")
    assert exc.value.status == 403


@respx.mock
async def test_failed_call_is_not_cached(client, tmp_path):
    """An error must not poison the cache for the next attempt."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(NaraApiError):
        await client.search(naId="1")
    assert cache_entries(tmp_path / "cache") == []


@respx.mock
async def test_a_rejected_call_still_counts_as_live(client):
    """The request reached the API; a budget that skips it undercounts."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(429, json={"message": "Too Many Requests"})
    )
    with pytest.raises(NaraApiError):
        await client.search(naId="1")
    assert client.live_calls == 1
    assert client.cache_hits == 0


@respx.mock
async def test_a_cache_write_leaves_only_the_entry(client, record, tmp_path):
    """The entry is written beside itself and renamed; nothing else remains."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="54765873")
    names = sorted(p.name for p in (tmp_path / "cache").iterdir())
    assert len(cache_entries(tmp_path / "cache")) == 1
    assert [n for n in names if not n.endswith(".json")] == [], names


def test_unwrap_handles_missing_body_envelope(record):
    """Some responses omit the outer 'body' key."""
    total, records = unwrap(search_payload(record)["body"])
    assert total == 1
    assert records[0]["naId"] == 54765873


def test_unwrap_empty_result():
    """No hits is a clean zero, not an exception."""
    assert unwrap({"body": {"hits": {"total": {"value": 0}, "hits": []}}}) == (0, [])


def test_unwrap_accepts_a_bare_integer_total(record):
    """Older Elasticsearch reports the total as a number, not an object."""
    payload = search_payload(record)
    payload["body"]["hits"]["total"] = 41234
    assert unwrap(payload)[0] == 41234


def test_unwrap_treats_an_unreadable_total_as_zero(record):
    """A total it cannot read must not turn a page of results into an error."""
    payload = search_payload(record)
    payload["body"]["hits"]["total"] = "lots"
    total, records = unwrap(payload)
    assert total == 0
    assert len(records) == 1


@respx.mock
async def test_get_fetches_the_path_it_is_given(client):
    """A non-search route is requested as itself, not funnelled through search."""
    route = respx.get(f"{API_BASE}/tags/naId/54765873").mock(
        return_value=httpx.Response(200, json={"body": {"hits": {"hits": []}}})
    )
    await client.get("/tags/naId/54765873")
    assert route.called
    assert route.calls.last.request.url.path.endswith("/tags/naId/54765873")


@respx.mock
async def test_cache_key_distinguishes_endpoints(client):
    """Two routes taking the same parameters must not share a cache entry."""
    respx.get(f"{API_BASE}/tags/naId/1").mock(
        return_value=httpx.Response(200, json={"kind": "tags"})
    )
    respx.get(f"{API_BASE}/comments/naId/1").mock(
        return_value=httpx.Response(200, json={"kind": "comments"})
    )
    tags = await client.get("/tags/naId/1")
    comments = await client.get("/comments/naId/1")
    assert (tags["kind"], comments["kind"]) == ("tags", "comments")
    assert client.live_calls == 2
    assert client.cache_hits == 0


@respx.mock
async def test_cache_hit_returns_identical_content(client, record):
    """A cached answer is the same answer, not a lossy re-render of it."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    first = await client.search(naId="54765873")
    second = await client.search(naId="54765873")
    assert first == second
    assert client.cache_hits == 1


@respx.mock
async def test_cache_key_distinguishes_every_parameter(client, record):
    """A parameter that does not reach the key silently serves a wrong answer."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    variants = [
        {"title": "Hall"},
        {"title": "Hall", "startDate": "1837"},
        {"title": "Hall", "startDate": "1838"},
        {"title": "Hall", "endDate": "1837"},
        {"title": "Hall", "availableOnline": True},
        {"title": "Hall", "availableOnline": False},
        {"title": "Hall", "tags_exist": True},
        {"title": "Hall", "page": 2},
        {"title": "Hall", "searchAfter": "*"},
        {"title": "Hall", "searchAfter": "12345"},
    ]
    for params in variants:
        await client.search(**params)
    assert client.live_calls == len(variants)
    assert client.cache_hits == 0


@respx.mock
async def test_false_is_a_filter_not_an_omission(client, record):
    """`tags_exist=false` means 'records nobody has tagged'; it must be sent."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(title="Hall", tags_exist=False, limit=None)
    params = route.calls.last.request.url.params
    assert params["tags_exist"] == "false"
    assert "limit" not in params


@respx.mock
async def test_corrupt_cache_entry_is_refetched_not_raised(client, record, tmp_path):
    """A truncated cache file must cost one call, not crash the tool."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="54765873")
    entry = cache_entries(tmp_path / "cache")[0]
    entry.write_text('{"body": {"hits":', encoding="utf-8")

    again = await client.search(naId="54765873")

    assert unwrap(again)[1][0]["naId"] == 54765873
    assert client.live_calls == 2
    assert client.cache_hits == 0
    assert json.loads(entry.read_text())  # the entry was repaired, not left broken


@respx.mock
async def test_refresh_bypasses_the_cache_and_replaces_the_entry(
    client, record, undigitised_record
):
    """The cache never expires, so refresh is the only way to see a change."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="54765873")
    route.mock(return_value=httpx.Response(200, json=search_payload(undigitised_record)))

    fresh = await client.search(naId="54765873", refresh=True)
    assert unwrap(fresh)[1][0]["naId"] == 2173628
    assert client.live_calls == 2

    again = await client.search(naId="54765873")
    assert again == fresh
    assert client.cache_hits == 1


@respx.mock
async def test_refresh_is_an_instruction_to_the_cache_not_a_query_parameter(client, record):
    """The Catalog must never see it."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.get("/records/search", naId="1", refresh=True)
    assert "refresh" not in route.calls.last.request.url.params


@respx.mock
async def test_a_failed_request_still_counts_as_spent(client):
    """A 500 reached the Catalog and spent quota; the budget must say so."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(NaraApiError):
        await client.search(naId="1")
    assert client.live_calls == 1
    assert client.month_ledger()[1] == 1


@respx.mock
async def test_the_month_ledger_outlives_the_client(client, record, tmp_path):
    """The point of the ledger: a new server on the same cache sees last week's calls."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="1")
    await client.search(naId="2")

    later = NaraClient("test-key", tmp_path / "cache", timeout=5.0)
    month, count = later.month_ledger()
    assert count == 2
    assert re.fullmatch(r"\d{4}-\d{2}", month)

    await later.search(naId="3")
    assert later.month_ledger()[1] == 3
    assert later.live_calls == 1


@respx.mock
async def test_a_ledger_from_another_month_restarts_at_zero(client, record, tmp_path):
    """The quota resets monthly; last month's spend must not carry over."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / BUDGET_FILE).write_text(json.dumps({"month": "2000-01", "live_calls": 9000}))
    assert client.month_ledger() == (current_month(), 0)

    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="1")
    data = json.loads((cache / BUDGET_FILE).read_text())
    assert (data["month"], data["live_calls"]) == (current_month(), 1)


@pytest.mark.parametrize(
    "contents",
    ["{not json", "[]", json.dumps({"month": None}), json.dumps({"live_calls": "12"})],
)
@respx.mock
async def test_a_corrupt_ledger_is_zero_and_then_repaired(client, record, tmp_path, contents):
    """A hand-edited or truncated ledger must cost nothing but its own history."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / BUDGET_FILE).write_text(contents)
    assert client.month_ledger()[1] == 0

    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="1")
    assert client.month_ledger()[1] == 1


@respx.mock
async def test_the_ledger_leaves_no_temporary_file_behind(client, record, tmp_path):
    """The atomic replace must not litter the cache with its scaffolding."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await client.search(naId="1")
    names = sorted(p.name for p in (tmp_path / "cache").iterdir())
    assert BUDGET_FILE in names
    assert [n for n in names if n.endswith((".tmp", ".partial"))] == []
    assert len(cache_entries(tmp_path / "cache")) == 1


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"message": "Invalid API key"}),
        (403, {"message": "Quota exceeded"}),
        (429, {"message": "Too Many Requests"}),
        (500, {"message": "Internal Server Error"}),
        (503, {"message": "Service Unavailable"}),
    ],
)
@respx.mock
async def test_every_failure_status_survives_to_the_caller(client, status, body):
    """The caller must be able to tell a bad key from a spent one."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(NaraApiError) as exc:
        await client.search(naId="1")
    assert exc.value.status == status
    assert exc.value.detail == body["message"]


@respx.mock
async def test_error_detail_falls_back_to_the_response_text(client):
    """An HTML error page still has to yield something a human can read."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(502, text="<html>bad gateway</html>")
    )
    with pytest.raises(NaraApiError) as exc:
        await client.search(naId="1")
    assert "bad gateway" in exc.value.detail


def test_unwrap_accepts_a_source_without_a_record_wrapper():
    """Contribution routes return the object directly under _source."""
    payload = {
        "body": {
            "hits": {
                "total": {"value": 1},
                "hits": [{"_source": {"contributionId": "x", "contribution": "Sarah Hall"}}],
            }
        }
    }
    total, records = unwrap(payload)
    assert total == 1
    assert records[0]["contribution"] == "Sarah Hall"


def test_unwrap_any_reads_the_digital_objects_container(extracted_text_payload):
    """/extractedText answers with a paginated object, not the search envelope."""
    items = unwrap_any(extracted_text_payload)
    assert [i["objectId"] for i in items] == ["8811", "8812"]


def test_unwrap_any_reads_a_bare_array():
    """A route answering with a list yields its objects, in order, minus junk."""
    assert unwrap_any([{"naId": 1}, "junk", None, {"naId": 2}]) == [{"naId": 1}, {"naId": 2}]


def test_unwrap_any_reads_the_search_envelope(tag_items):
    """The contribution routes reuse the search envelope."""
    assert len(unwrap_any(contribution_payload(*tag_items))) == 2


def test_unwrap_any_tolerates_nothing_useful():
    """Junk in means an empty list out, never an exception."""
    assert unwrap_any(None) == []
    assert unwrap_any([]) == []
    assert unwrap_any({}) == []
    assert unwrap_any("nope") == []


def test_cursor_is_the_last_hits_sort_value(record):
    """searchAfter continues from the sort key of the final hit on the page."""
    assert cursor(search_payload(record, sort=[1727000000000, "54765873"])) == (
        "1727000000000,54765873"
    )


def test_cursor_is_none_when_the_page_cannot_be_continued(record):
    """No sort key means no cursor, rather than an invented one."""
    assert cursor(search_payload(record)) is None
    assert cursor(search_payload()) is None
