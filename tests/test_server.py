"""Tool behaviour: argument mapping, shaping and error envelopes.

Tools are invoked through ``mcp.call_tool`` rather than as plain functions, so
declared defaults are resolved the way a real client resolves them. The
``served`` fixture supplies a client with a throwaway cache, so no test reads
the developer's own cache or needs an API key.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from nara_catalog_mcp import server
from nara_catalog_mcp.client import API_BASE, BUDGET_FILE, current_month

from .conftest import call_tool, contribution_payload, search_payload


def _ok(json_body):
    return httpx.Response(200, json=json_body)


async def test_every_tool_is_registered():
    """The published surface is the one the README and the design notes describe."""
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == {
        "search_records",
        "search_records_advanced",
        "get_record",
        "get_record_images",
        "get_extracted_text",
        "get_transcriptions",
        "get_tags",
        "get_comments",
        "search_transcriptions",
        "search_tags",
        "search_extracted_text",
        "search_comments",
        "browse_children",
        "get_online_availability",
        "get_partner_digital_objects",
        "api_budget",
        "download_page_image",
        "search_by_contribution_text",
        "aad_list_series",
        "aad_search",
        "aad_get_record",
    }


async def test_no_write_tool_is_exposed():
    """The API can post tags and transcriptions; this server must not.

    A contribution publishes under the operator's account and cannot be
    withdrawn from here, so the write paths stay out of the default surface.
    """
    names = {t.name for t in await server.mcp.list_tools()}
    forbidden = [
        n
        for n in names
        if n.split("_")[0]
        in {"add", "post", "create", "update", "delete", "put", "submit", "contribute"}
    ]
    assert forbidden == []


# --------------------------------------------------------------------------
# search_records
# --------------------------------------------------------------------------


async def test_search_without_criteria_is_refused():
    """An empty search would page through the whole Catalog; refuse it."""
    assert (await call_tool("search_records"))["error"] == "no_criteria"


@respx.mock
async def test_search_returns_summaries(served, record):
    """A hit comes back shaped for triage, with its total."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record, total=1)))
    out = await call_tool("search_records", title="Joshua Hall")
    assert out["total"] == 1
    assert out["records"][0]["naid"] == 54765873


@respx.mock
async def test_search_limit_is_clamped(served, record):
    """An over-large limit is clamped rather than passed through."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records", title="x", limit=5000)
    assert route.calls.last.request.url.params["limit"] == "100"


@respx.mock
async def test_search_pages_rather_than_offsets(served, record):
    """The Catalog takes `page`, not `offset`; an offset is silently ignored."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records", title="x", page=3)
    params = route.calls.last.request.url.params
    assert params["page"] == "3"
    assert "offset" not in params


@respx.mock
async def test_page_is_never_below_one(served, record):
    """Page 0 is not a thing; clamp rather than send a value the API rejects."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records", title="x", page=0)
    assert route.calls.last.request.url.params["page"] == "1"


# --------------------------------------------------------------------------
# search_records_advanced
# --------------------------------------------------------------------------


@respx.mock
async def test_advanced_search_without_criteria_never_calls_the_api(served):
    """Refusing locally protects the monthly quota as well as the caller."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload()))
    out = await call_tool("search_records_advanced", limit=5)
    assert out["error"] == "no_criteria"
    assert not route.called


@respx.mock
async def test_advanced_search_maps_filters_to_api_parameter_names(served, record):
    """Every documented filter must arrive under the name the API expects."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool(
        "search_records_advanced",
        title="Hall",
        start_date="1837",
        end_date="1853",
        available_online=True,
        local_identifier="W.17050",
        microform_publication="M804",
        record_group_number="15",
        collection_identifier="HOOVER",
        ancestor_naid="300020",
        level_of_description="fileUnit",
        reference_units="National Archives Building",
        creators="Bureau of Pensions",
        geographic_reference="York County (Me.)",
        transcriptions_exist=True,
    )
    params = route.calls.last.request.url.params
    assert params["startDate"] == "1837"
    assert params["endDate"] == "1853"
    assert params["availableOnline"] == "true"
    assert params["localIdentifier"] == "W.17050"
    assert params["microformPublicationsIdentifier"] == "M804"
    assert params["recordGroupNumber"] == "15"
    assert params["collectionIdentifier"] == "HOOVER"
    assert params["ancestorNaId"] == "300020"
    assert params["levelOfDescription"] == "fileUnit"
    assert params["referenceUnits"] == "National Archives Building"
    assert params["creators"] == "Bureau of Pensions"
    assert params["geographicReference"] == "York County (Me.)"
    assert params["transcriptions_exist"] == "true"


@respx.mock
async def test_advanced_search_sends_a_false_contribution_filter(served, record):
    """`tags_exist=false` is a real query: records nobody has tagged."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", title="Hall", tags_exist=False)
    assert route.calls.last.request.url.params["tags_exist"] == "false"


@respx.mock
async def test_advanced_search_omits_filters_that_were_not_given(served, record):
    """An unset filter must not become an empty string the API tries to match."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", title="Hall")
    params = route.calls.last.request.url.params
    for absent in ("startDate", "availableOnline", "tags_exist", "exactDate"):
        assert absent not in params


@pytest.mark.parametrize("bad", ["1837/05", "May 1837", "18370501", "1837-5"])
@respx.mock
async def test_advanced_search_rejects_a_malformed_date(served, bad):
    """A date the API would reject is caught here, before a call is spent."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload()))
    out = await call_tool("search_records_advanced", title="Hall", start_date=bad)
    assert out["error"] == "invalid_date"
    assert not route.called


@respx.mock
async def test_advanced_search_refuses_an_exact_date_with_a_range(served):
    """The API cannot combine them, and silently ignoring one would mislead."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload()))
    out = await call_tool(
        "search_records_advanced",
        title="Hall",
        exact_date="1837-07-14",
        start_date="1837",
    )
    assert out["error"] == "conflicting_dates"
    assert not route.called


@respx.mock
async def test_advanced_search_rejects_an_unknown_level(served):
    """'folder' is not a Catalog level; say which words are."""
    out = await call_tool("search_records_advanced", title="Hall", level_of_description="folder")
    assert out["error"] == "invalid_level"
    assert "fileUnit" in out["message"]


@respx.mock
async def test_advanced_search_cursor_replaces_page(served, record):
    """searchAfter and page cannot be sent together; the cursor wins."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(record, sort=[17, "54765873"]))
    )
    out = await call_tool("search_records_advanced", title="Hall", search_after="*", page=4)
    params = route.calls.last.request.url.params
    assert params["searchAfter"] == "*"
    assert "page" not in params
    assert out["next_search_after"] == "17,54765873"


@respx.mock
async def test_search_after_cursor_round_trips(served, record, undigitised_record):
    """The cursor a page hands back must be the cursor the next page accepts.

    This is the only way past the 10,000-result wall, and a surname sweep hits
    it, so the round trip is worth holding to.
    """
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(record, total=41234, sort=[99]))
    )
    first = await call_tool("search_records_advanced", title="Hall", search_after="*")
    assert first["next_search_after"] == "99"

    route.mock(return_value=_ok(search_payload(undigitised_record, total=41234, sort=[188])))
    second = await call_tool(
        "search_records_advanced",
        title="Hall",
        search_after=first["next_search_after"],
    )
    assert route.calls.last.request.url.params["searchAfter"] == "99"
    assert second["records"][0]["naid"] == 2173628
    assert second["next_search_after"] == "188"


@respx.mock
async def test_advanced_search_warns_when_page_paging_runs_out(served, record):
    """Past 10,000 hits `page` stops working; say so instead of looping."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(record, total=41234))
    )
    out = await call_tool("search_records_advanced", title="Hall")
    assert "search_after" in out["paging_note"]


@respx.mock
async def test_advanced_search_does_not_warn_within_the_page_limit(served, record):
    """A note on every small search is noise; it belongs at the boundary."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record, total=12)))
    assert "paging_note" not in await call_tool("search_records_advanced", title="H")


@respx.mock
async def test_include_extracted_text_drops_the_lean_field_list(served, record):
    """OCR text lives outside the lean field set, so asking for it widens it."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    params = route.calls.last.request.url.params
    assert params["includeExtractedText"] == "true"
    assert "sourceIncludes" not in params


@respx.mock
async def test_include_extracted_text_carries_the_text_into_each_hit(served, record):
    """Paying for the wider response and then dropping the text is the worst of both.

    The search route uses a flat `extractedText` on each digital object
    (verified live 2026-09-28); /extractedText nests the same text at
    `otherExtractedText.contribution`. Both spellings land in the same place.
    """
    record["digitalObjects"][0]["extractedText"] = "Declaration of Sarah Hall, widow"
    record["digitalObjects"][1]["otherExtractedText"] = {"contribution": "Berwick, 1782"}
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    texts = out["records"][0]["extracted_text"]
    assert [(t["object_id"], t["text"]) for t in texts] == [
        ("8811", "Declaration of Sarah Hall, widow"),
        ("8812", "Berwick, 1782"),
    ]
    assert "OCR" in out["caution"]


@respx.mock
async def test_folded_text_reads_a_partner_only_hit(served, record):
    """A search hit lists partner text; a record with only that must not fold empty."""
    record["digitalObjects"][0]["otherExtractedText"] = [
        {"contribution": "Berwick, 1782", "contributor": {"partnerFullName": "FamilySearch"}}
    ]
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    (entry,) = out["records"][0]["extracted_text"]
    assert entry["text"] == "Berwick, 1782"
    assert entry["contributed_by"] == "FamilySearch"


@respx.mock
async def test_folded_text_is_clipped_per_page_and_says_so(served, record):
    """A hundred hits of whole pages would swamp the response; clip, and flag it."""
    record["digitalObjects"][0]["extractedText"] = "x" * (server.SEARCH_TEXT_LIMIT + 1)
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    entry = out["records"][0]["extracted_text"][0]
    assert len(entry["text"]) == server.SEARCH_TEXT_LIMIT
    assert entry["characters"] == server.SEARCH_TEXT_LIMIT + 1
    assert entry["truncated"] is True


@respx.mock
async def test_extracted_text_is_absent_unless_asked_for(served, record):
    """Without the flag the key is absent, so its absence carries meaning."""
    record["digitalObjects"][0]["extractedText"] = "text a lean search never sees"
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall")
    assert "extracted_text" not in out["records"][0]
    assert "caution" not in out


@respx.mock
async def test_folded_text_on_a_hit_without_text_is_an_empty_list(served, record):
    """A hit with no OCR reports an empty list, not a missing key or a stub."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    assert out["records"][0]["extracted_text"] == []


@respx.mock
async def test_advanced_search_rejects_a_non_numeric_ancestor(served):
    """An ancestor NAID that is not digits is a typo, not a query."""
    out = await call_tool("search_records_advanced", title="Hall", ancestor_naid="RG15")
    assert out["error"] == "invalid_naid"


@respx.mock
async def test_advanced_search_sends_what_it_validated(served, record):
    """A value accepted once stripped must be sent stripped, not as typed."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool(
        "search_records_advanced",
        title="Hall",
        start_date=" 1837 ",
        end_date="1853\n",
        ancestor_naid=" 300020",
    )
    params = route.calls.last.request.url.params
    assert params["startDate"] == "1837"
    assert params["endDate"] == "1853"
    assert params["ancestorNaId"] == "300020"


# --------------------------------------------------------------------------
# get_record / get_record_images
# --------------------------------------------------------------------------


@respx.mock
async def test_get_record_missing_naid_is_not_found(served):
    """An unknown NAID is a clean not_found, not an exception."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload()))
    out = await call_tool("get_record", naid=" 404404 ")
    assert out["error"] == "not_found"
    assert out["message"] == "No record with NAID 404404."


@respx.mock
async def test_get_record_images_pairs_each_url_with_its_object_id(served, record):
    """The image list is what a caller cites from, and the ids are what OCR entries name."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("get_record_images", naid="54765873")
    assert out["image_count"] == 2
    assert [(i["page"], i["object_id"]) for i in out["images"]] == [(1, "8811"), (2, "8812")]
    assert out["images"][0]["url"].endswith("/1.jpg")


@respx.mock
async def test_undigitised_record_reports_no_images(served, undigitised_record):
    """Zero images is an answer -- order the paper -- not a failure."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(undigitised_record))
    )
    out = await call_tool("get_record", naid="2173628")
    assert out["images"] == []
    assert out["reference_units"] == ["National Archives at St. Louis"]


# --------------------------------------------------------------------------
# the evidence path: extracted text and contributions
# --------------------------------------------------------------------------


@respx.mock
async def test_get_extracted_text_returns_a_page_per_object(served, extracted_text_payload):
    """OCR comes back per digital object, in the order the Catalog lists it."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(return_value=_ok(extracted_text_payload))
    out = await call_tool("get_extracted_text", naid="54765873")
    assert out["object_count"] == 2
    assert [p["object_id"] for p in out["pages"]] == ["8811", "8812"]


@respx.mock
async def test_get_extracted_text_truncates_and_warns(served, extracted_text_payload):
    """A 5,000-character page is clipped, and the answer says OCR is a lead."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(return_value=_ok(extracted_text_payload))
    out = await call_tool("get_extracted_text", naid="54765873", max_chars=50)
    assert out["pages"][0]["truncated"] is True
    assert len(out["pages"][0]["text"]) == 50
    assert "OCR" in out["caution"]


@respx.mock
async def test_get_extracted_text_can_scope_to_one_object(served, extracted_text_payload):
    """One page of a 300-page file is often all that is wanted."""
    route = respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=_ok(extracted_text_payload)
    )
    await call_tool("get_extracted_text", naid="54765873", object_id="8812")
    assert route.calls.last.request.url.params["objectId"] == "8812"


@respx.mock
async def test_get_extracted_text_rejects_a_non_numeric_object_id(served):
    """objectId reaches the query string; it has to be a number."""
    out = await call_tool("get_extracted_text", naid="1", object_id="all")
    assert out["error"] == "invalid_object_id"


@respx.mock
async def test_get_transcriptions_returns_text_and_author(served, transcription_items):
    """Who typed it is part of judging it, so it comes back with the text."""
    respx.get(f"{API_BASE}/transcriptions/naId/54765873").mock(
        return_value=_ok(contribution_payload(*transcription_items))
    )
    out = await call_tool("get_transcriptions", naid="54765873")
    assert out["count"] == 2
    assert out["transcriptions"][0]["contributor"] == "careful_reader"
    assert "Sarah Hall" in out["transcriptions"][0]["text"]


@respx.mock
async def test_get_transcriptions_honours_max_chars(served, transcription_items):
    """A transcribed file unit can be enormous; the caller sets the budget."""
    respx.get(f"{API_BASE}/transcriptions/naId/54765873").mock(
        return_value=_ok(contribution_payload(*transcription_items))
    )
    out = await call_tool("get_transcriptions", naid="54765873", max_chars=10)
    assert out["transcriptions"][0]["truncated"] is True


@respx.mock
async def test_get_tags_returns_the_tagged_names(served, tag_items):
    """On a pension file the tags are usually the people inside it."""
    respx.get(f"{API_BASE}/tags/naId/54765873").mock(
        return_value=_ok(contribution_payload(*tag_items))
    )
    out = await call_tool("get_tags", naid="54765873")
    assert [t["text"] for t in out["tags"]] == ["Sarah Hall", "Berwick, Maine"]


@respx.mock
async def test_get_comments_returns_the_note(served, comment_items):
    """A comment often names the related file you would not otherwise find."""
    respx.get(f"{API_BASE}/comments/naId/54765873").mock(
        return_value=_ok(contribution_payload(*comment_items))
    )
    out = await call_tool("get_comments", naid="54765873")
    assert out["count"] == 1
    assert "bounty land" in out["comments"][0]["text"]


@respx.mock
async def test_contributions_unwrap_from_either_envelope(served, tag_items):
    """Some routes nest the object under _source.record; accept both."""
    respx.get(f"{API_BASE}/tags/naId/54765873").mock(
        return_value=_ok(contribution_payload(*tag_items, wrapped=True))
    )
    out = await call_tool("get_tags", naid="54765873")
    assert out["tags"][0]["text"] == "Sarah Hall"


@respx.mock
async def test_a_record_with_no_contributions_is_an_empty_list(served):
    """Nobody has tagged it is an answer; it is not an error."""
    respx.get(f"{API_BASE}/tags/naId/54765873").mock(return_value=_ok(contribution_payload()))
    out = await call_tool("get_tags", naid="54765873")
    assert out == {
        "naid": 54765873,
        "count": 0,
        "tags": [],
        "catalog_url": "https://catalog.archives.gov/id/54765873",
    }


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_record",
        "get_record_images",
        "get_extracted_text",
        "get_transcriptions",
        "get_tags",
        "get_comments",
        "get_online_availability",
    ],
)
@respx.mock
async def test_a_non_numeric_naid_never_reaches_the_api(served, tool_name):
    """The NAID is interpolated into the path, so it is checked before sending."""
    route = respx.route(host="catalog.archives.gov").mock(return_value=_ok({"body": {}}))
    out = await call_tool(tool_name, naid="../../users/userId/7")
    assert out["error"] == "invalid_naid"
    assert not route.called


@pytest.mark.parametrize("naid", ["²", "٣٤٥", "１２３"])
@pytest.mark.parametrize("tool_name", ["get_record", "get_tags", "get_partner_digital_objects"])
@respx.mock
async def test_digits_from_other_scripts_are_not_a_naid(served, tool_name, naid):
    """Superscript, Arabic-Indic and full-width digits pass str.isdigit.

    None is a NAID the Catalog can resolve, and int() rejects some of them --
    which used to happen only after the request had been sent and paid for.
    """
    route = respx.route(host="catalog.archives.gov").mock(return_value=_ok({"body": {}}))
    out = await call_tool(tool_name, naid=naid)
    assert out["error"] == "invalid_naid"
    assert not route.called


@respx.mock
async def test_an_object_id_must_be_ascii_digits_too(served):
    """objectId goes into the query string; hold it to the same rule."""
    route = respx.route(host="catalog.archives.gov").mock(return_value=_ok({"body": {}}))
    out = await call_tool("get_extracted_text", naid="54765873", object_id="８８１１")
    assert out["error"] == "invalid_object_id"
    assert not route.called


# --------------------------------------------------------------------------
# searching what people wrote
# --------------------------------------------------------------------------


@respx.mock
async def test_search_transcriptions_returns_record_summaries(served, record):
    """The by-* routes answer with records, so triage them like any search."""
    respx.get(f"{API_BASE}/records/search/by-transcription").mock(
        return_value=_ok(search_payload(record, total=1))
    )
    out = await call_tool("search_transcriptions", query="widow Sarah")
    assert out["total"] == 1
    assert out["matched_on"] == "transcription"
    assert out["records"][0]["naid"] == 54765873


@respx.mock
async def test_search_transcriptions_sends_the_query(served, record):
    """Boolean and phrase syntax must survive to the API unaltered."""
    route = respx.get(f"{API_BASE}/records/search/by-transcription").mock(
        return_value=_ok(search_payload(record))
    )
    await call_tool("search_transcriptions", query='"Sarah Hall" AND Berwick')
    assert route.calls.last.request.url.params["q"] == '"Sarah Hall" AND Berwick'


@respx.mock
async def test_search_tags_can_match_a_tag_exactly(served, record):
    """A tag is short, so an exact match is sharper than a word match."""
    route = respx.get(f"{API_BASE}/records/search/by-tag").mock(
        return_value=_ok(search_payload(record))
    )
    await call_tool("search_tags", tag_is="Sarah Hall")
    assert route.calls.last.request.url.params["tag_is"] == "Sarah Hall"


@respx.mock
async def test_search_comments_can_filter_to_one_contributor(served, record):
    """Following one researcher's trail is a real way to work."""
    route = respx.get(f"{API_BASE}/records/search/by-comment").mock(
        return_value=_ok(search_payload(record))
    )
    await call_tool("search_comments", query="Hall", contributor="archivist_jo")
    assert route.calls.last.request.url.params["userName"] == "archivist_jo"


@respx.mock
async def test_search_extracted_text_requires_a_query(served):
    """That endpoint has no other criterion; refuse before spending a call."""
    route = respx.get(f"{API_BASE}/records/search/by-other-extracted-text").mock(
        return_value=_ok(search_payload())
    )
    out = await call_tool("search_extracted_text", query="   ")
    assert out["error"] == "no_criteria"
    assert not route.called


@pytest.mark.parametrize("tool_name", ["search_transcriptions", "search_tags", "search_comments"])
async def test_contribution_searches_refuse_an_empty_call(served, tool_name):
    """No criteria means the whole contribution corpus; refuse it."""
    assert (await call_tool(tool_name))["error"] == "no_criteria"


@pytest.mark.parametrize("tool_name", ["search_transcriptions", "search_tags", "search_comments"])
@respx.mock
async def test_contribution_searches_check_a_naid_filter(served, tool_name):
    """A NAID filter that is not digits would spend a call to match nothing."""
    route = respx.route(host="catalog.archives.gov").mock(return_value=_ok(search_payload()))
    out = await call_tool(tool_name, naid="W.17050")
    assert out["error"] == "invalid_naid"
    assert not route.called


@respx.mock
async def test_contribution_searches_send_a_clean_naid(served, record):
    """Whitespace around a valid NAID is dropped, not sent."""
    route = respx.get(f"{API_BASE}/records/search/by-tag").mock(
        return_value=_ok(search_payload(record))
    )
    await call_tool("search_tags", naid=" 54765873 ")
    assert route.calls.last.request.url.params["naId"] == "54765873"


@respx.mock
async def test_each_contribution_search_uses_its_own_endpoint(served, record):
    """A tag search that quietly ran against comments would look plausible."""
    routes = {
        kind: respx.get(f"{API_BASE}/records/search/by-{kind}").mock(
            return_value=_ok(search_payload(record))
        )
        for kind in ("transcription", "tag", "comment")
    }
    await call_tool("search_transcriptions", query="x")
    await call_tool("search_tags", query="x")
    await call_tool("search_comments", query="x")
    assert all(r.called for r in routes.values())


# --------------------------------------------------------------------------
# hierarchy and availability
# --------------------------------------------------------------------------


@respx.mock
async def test_browse_children_lists_the_next_level_down(served, series_record, child_records):
    """A series resolves to its file units, which is how you walk to a person."""
    respx.get(f"{API_BASE}/records/parentNaId/300020").mock(
        return_value=_ok(search_payload(*child_records, total=2))
    )
    out = await call_tool("browse_children", parent_naid="300020")
    assert out["parent_naid"] == 300020
    assert out["total"] == 2
    assert [c["naid"] for c in out["children"]] == [300021, 300022]
    assert out["children"][0]["level"] == "fileUnit"


@respx.mock
async def test_browse_children_paginates(served, child_records):
    """A record group has thousands of children; paging is not optional."""
    route = respx.get(f"{API_BASE}/records/parentNaId/300020").mock(
        return_value=_ok(search_payload(*child_records))
    )
    await call_tool("browse_children", parent_naid="300020", page=2, limit=500)
    params = route.calls.last.request.url.params
    assert (params["page"], params["limit"]) == ("2", "100")


@respx.mock
async def test_browse_children_rejects_a_bad_parent(served):
    """A parent NAID is interpolated into the path like any other."""
    out = await call_tool("browse_children", parent_naid="series-15")
    assert out["error"] == "invalid_naid"


@respx.mock
async def test_get_online_availability_lists_other_homes(served):
    """Resolving a partner's index entry back to the original is the point."""
    respx.get(f"{API_BASE}/online-availability/naId/54765873").mock(
        return_value=_ok(
            [
                {
                    "naId": 54765873,
                    "status": "active",
                    "availability": "fullyDigitized",
                    "onlineResources": [{"url": "https://partner.example/w17050"}],
                }
            ]
        )
    )
    out = await call_tool("get_online_availability", naid="54765873")
    assert out["count"] == 1
    assert out["availability"][0]["urls"] == ["https://partner.example/w17050"]


# --------------------------------------------------------------------------
# budget and error envelopes
# --------------------------------------------------------------------------


@respx.mock
async def test_api_budget_counts_a_mixed_hit_and_miss_sequence(served, record):
    """Two distinct queries and one repeat is two calls and one hit."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records", title="Hall")
    await call_tool("search_records", title="Hall")
    await call_tool("search_records", title="Tozier")
    out = await call_tool("api_budget")
    assert out["live_calls_this_session"] == 2
    assert out["cache_hits_this_session"] == 1
    assert out["monthly_call_budget"] == 10_000


@respx.mock
async def test_api_budget_reports_the_month_from_the_ledger(served, record):
    """The month's figure is what survives a restart, and what the cap is on."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records", title="Hall")
    await call_tool("search_records", title="Hall")
    await call_tool("search_records", title="Tozier")
    out = await call_tool("api_budget")
    assert out["live_calls_this_month"] == 2
    assert out["remaining_this_month"] == 9_998
    assert re.fullmatch(r"\d{4}-\d{2}", out["month"])


@respx.mock
async def test_api_budget_says_what_the_ledger_cannot_see(served):
    """The ledger spans sessions on one cache directory; claiming more would mislead."""
    out = await call_tool("api_budget")
    assert "cache directory" in out["note"]
    assert "machine" in out["note"]
    assert out["live_calls_this_session"] == 0
    assert out["live_calls_this_month"] == 0


async def test_remaining_budget_never_goes_negative(served, tmp_path):
    """A key past its cap has nothing left, not a negative balance."""
    cache = tmp_path / "server-cache"
    cache.mkdir(exist_ok=True)
    (cache / BUDGET_FILE).write_text(json.dumps({"month": current_month(), "live_calls": 10_500}))
    out = await call_tool("api_budget")
    assert out["live_calls_this_month"] == 10_500
    assert out["remaining_this_month"] == 0


# --------------------------------------------------------------------------
# refresh: re-reading one answer without deleting the cache
# --------------------------------------------------------------------------
#: Every tool that reads one record, with the route it reads from. Each
#: caches an answer that can go stale: a description NARA edits, a file that
#: gets digitised, transcriptions and tags that accumulate.
REFRESHABLE = [
    ("get_record", "/records/search"),
    ("get_record_images", "/records/search"),
    ("get_extracted_text", "/extractedText/54765873"),
    ("get_transcriptions", "/transcriptions/naId/54765873"),
    ("get_tags", "/tags/naId/54765873"),
    ("get_comments", "/comments/naId/54765873"),
]


@pytest.mark.parametrize(("tool_name", "path"), REFRESHABLE)
@respx.mock
async def test_refresh_re_reads_the_catalog_and_is_never_sent_to_it(
    served, record, tool_name, path
):
    """A cached answer that can go stale must be re-readable one at a time."""
    route = respx.get(f"{API_BASE}{path}").mock(return_value=_ok(search_payload(record)))
    await call_tool(tool_name, naid="54765873")
    await call_tool(tool_name, naid="54765873")
    assert route.call_count == 1, "the second plain read should have been a cache hit"

    await call_tool(tool_name, naid="54765873", refresh=True)
    assert route.call_count == 2
    assert "refresh" not in route.calls.last.request.url.params
    assert (served.live_calls, served.cache_hits) == (2, 1)


async def test_every_single_record_reader_can_refresh():
    """The flag belongs on each tool whose cached answer can go stale, and no other."""
    tools = await server.mcp.list_tools()
    with_flag = {
        t.name for t in tools if "refresh" in ((t.input_schema or {}).get("properties") or {})
    }
    # aad_get_record reads one AAD row, which a reload can change; its own
    # tests cover the flag.
    assert with_flag == {name for name, _ in REFRESHABLE} | {"aad_get_record"}


@respx.mock
async def test_every_advanced_filter_reaches_the_cache_key(served, record):
    """A filter that does not change the cache key silently serves a stale answer.

    Driven by the tool's own schema rather than a hand-kept list, so a
    parameter added later is covered the day it lands. Every variant must be
    a live call: one cache hit here is one wrong answer in use.
    """
    from .conftest import _value_for, assert_reached_body

    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    tool = next(t for t in await server.mcp.list_tools() if t.name == "search_records_advanced")
    props = (tool.input_schema or {}).get("properties") or {}
    # Values the generic builder cannot invent: a real level, the cursor's
    # required first value, and non-default numbers. Booleans are forced to
    # True because False is the declared default for one of them, and a
    # default reproduces the baseline call rather than varying it.
    forced = {"level_of_description": "item", "search_after": "*", "page": 2, "limit": 5}

    await call_tool("search_records_advanced", title="Hall")  # the baseline
    tried = []
    for name, spec in props.items():
        if name == "title":
            continue
        types = {spec.get("type"), *(s.get("type") for s in spec.get("anyOf") or [])}
        if name in forced:
            value = forced[name]
        elif "boolean" in types:
            value = True
        else:
            value = _value_for(name, spec)
        out = await call_tool("search_records_advanced", title="Hall", **{name: value})
        assert_reached_body(tool.name, out)
        assert "error" not in out, f"{name}={value!r} was refused: {out}"
        tried.append(name)

    assert len(tried) == len(props) - 1
    assert served.live_calls == len(tried) + 1, "a variant was served from the cache"
    assert served.cache_hits == 0


@respx.mock
async def test_a_cached_repeat_does_not_spend_quota(served, record):
    """The cache exists to protect a capped key, not to be fast."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(record, total=1))
    )
    first = await call_tool("get_record", naid="54765873")
    second = await call_tool("get_record", naid="54765873")
    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_api_error_is_returned_not_raised(served):
    """A tool answers with a structured error rather than raising."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(429, json={"message": "Too Many Requests"})
    )
    out = await call_tool("search_records", title="anything")
    assert out["error"] == "api_error"
    assert out["status"] == 429


@pytest.mark.parametrize(
    ("status", "expected_word"),
    [
        (401, "key"),
        (403, "quota"),
        (404, "no such record"),
        (429, "Rate limited"),
        (500, "Retry"),
    ],
)
@respx.mock
async def test_each_failure_status_is_distinguishable(served, status, expected_word):
    """'Your key is wrong' and 'your key is spent' must not read the same."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=httpx.Response(status, json={"message": "no"})
    )
    out = await call_tool("search_records", title="anything")
    assert out["status"] == status
    assert expected_word.lower() in out["meaning"].lower()


@respx.mock
async def test_a_transport_failure_is_an_envelope_too(served):
    """A dropped connection is not an API error, and still must not raise."""
    respx.get(f"{API_BASE}/records/search").mock(side_effect=httpx.ConnectError("no route to host"))
    out = await call_tool("search_records", title="anything")
    assert out["error"] == "unexpected"
    assert "no route to host" in out["message"]


async def test_a_missing_api_key_is_reported_as_configuration(monkeypatch):
    """The first call, not import, is where a missing key surfaces."""
    monkeypatch.setattr(server.state, "client", None)
    monkeypatch.setattr(server.state, "config", None)
    monkeypatch.delenv("NARA_API_KEY", raising=False)
    monkeypatch.setattr("nara_catalog_mcp.config.load_dotenv", lambda *a, **k: False)
    out = await call_tool("get_record", naid="1")
    assert out["error"] == "not_configured"
    assert "NARA_API_KEY" in out["message"]


@respx.mock
async def test_availability_with_nothing_recorded_returns_a_note(served):
    """A record with no online availability answers in prose, not with a list.

    Shape captured from the live Catalog on 2026-09-23. Treating the message
    as an entry would produce one junk row per lookup.
    """
    respx.get(f"{API_BASE}/online-availability/naId/54765873").mock(
        return_value=httpx.Response(
            200,
            json={"response": "No Online Availability status available for naId 54765873."},
        )
    )
    out = await call_tool("get_online_availability", naid="54765873")
    assert out["count"] == 0
    assert out["availability"] == []
    assert "No Online Availability" in out["note"]


@respx.mock
async def test_extracted_text_returns_every_page_it_was_given(served):
    """Against the real envelope this must yield pages, not one empty stub."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(
            200,
            json={
                "naId": "54765873",
                "total": 58,
                "digitalObjects": [
                    {"objectId": "1", "otherExtractedText": {"contribution": "first page text"}},
                    {"objectId": "2", "otherExtractedText": {"contribution": "second page text"}},
                ],
            },
        )
    )
    out = await call_tool("get_extracted_text", naid="54765873")
    assert out["object_count"] == 2
    assert out["pages"][1]["text"] == "second page text"


def _ocr_stubs(n: int) -> list[dict]:
    return [{"objectId": str(i), "otherExtractedText": {"contribution": "t"}} for i in range(n)]


@pytest.mark.parametrize("total", [58, "58"])
@respx.mock
async def test_extracted_text_reports_the_total_and_the_next_results_page(served, total):
    """A 58-page file at 20 objects a call is three calls.

    Left unsaid, the caller reads twenty pages and believes it has read the
    file. The Catalog spells the count as an integer; a string is accepted
    too, since the same object carries naId as a string.
    """
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(
            200,
            json={
                "naId": "54765873",
                "page": 1,
                "limit": 20,
                "total": total,
                "digitalObjects": _ocr_stubs(20),
            },
        )
    )
    out = await call_tool("get_extracted_text", naid="54765873")
    assert out["object_count"] == 20
    assert out["total_objects"] == 58
    assert out["next_results_page"] == 2
    assert "page=2" in out["paging_note"]


@respx.mock
async def test_extracted_text_last_results_page_offers_no_next(served, extracted_text_payload):
    """Two objects of a total of two: nothing more to fetch, so no note."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(200, json=extracted_text_payload)
    )
    out = await call_tool("get_extracted_text", naid="54765873")
    assert out["total_objects"] == 2
    assert "next_results_page" not in out
    assert "paging_note" not in out


@respx.mock
async def test_extracted_text_paging_arithmetic_uses_the_clamped_limit(served):
    """The remainder must come from the limit actually sent, not the one asked for."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(
            200,
            json={"naId": "54765873", "total": 150, "digitalObjects": _ocr_stubs(100)},
        )
    )
    out = await call_tool("get_extracted_text", naid="54765873", limit=500)
    assert out["object_count"] == 100
    assert out["next_results_page"] == 2
    assert out["paging_note"].startswith("50 more")


@respx.mock
async def test_a_read_scoped_to_one_object_never_offers_a_next_page(served):
    """Whatever `total` describes, a one-object read has nothing to page through.

    Sending the caller to page=2 of a single object would spend calls on
    empty answers.
    """
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(
            200,
            json={"naId": "54765873", "total": 58, "digitalObjects": _ocr_stubs(1)},
        )
    )
    out = await call_tool("get_extracted_text", naid="54765873", object_id="8811")
    assert out["object_count"] == 1
    assert "next_results_page" not in out
    assert "paging_note" not in out


@respx.mock
async def test_extracted_text_without_a_total_falls_back_to_what_it_got(served):
    """A payload that omits the count is not a reason to invent one, or to raise."""
    respx.get(f"{API_BASE}/extractedText/54765873").mock(
        return_value=httpx.Response(200, json={"digitalObjects": _ocr_stubs(3)})
    )
    out = await call_tool("get_extracted_text", naid="54765873")
    assert out["total_objects"] == 3
    assert "next_results_page" not in out


@respx.mock
async def test_partner_objects_are_listed(served):
    """A partner match tells you imagery exists where NARA's own pages do not."""
    respx.get(f"{API_BASE}/metadata/naid-availability/naid/54765873").mock(
        return_value=_ok({"ids": ["8811", "8812"]})
    )
    out = await call_tool("get_partner_digital_objects", naid="54765873")
    assert out["object_count"] == 2
    assert out["digital_object_ids"] == ["8811", "8812"]


@respx.mock
async def test_partner_objects_empty_list_is_not_an_error(served):
    """No match is the common answer, not a failure."""
    respx.get(f"{API_BASE}/metadata/naid-availability/naid/54765873").mock(
        return_value=_ok({"ids": []})
    )
    out = await call_tool("get_partner_digital_objects", naid="54765873")
    assert out["object_count"] == 0
    assert "error" not in out


@respx.mock
async def test_partner_objects_404_reads_the_same_as_an_empty_list(served):
    """The API answers 404 where nothing matched. That is not an error either.

    Verified live 2026-09-23: an empty list and a 404 both mean 'no partner
    metadata', so a caller should not have to distinguish them.
    """
    respx.get(f"{API_BASE}/metadata/naid-availability/naid/1").mock(
        return_value=httpx.Response(404, json={"message": "No matching records"})
    )
    out = await call_tool("get_partner_digital_objects", naid="1")
    assert out["object_count"] == 0
    assert "error" not in out
    assert "No partner metadata" in out["note"]


@respx.mock
async def test_partner_objects_other_errors_still_surface(served):
    """Only 404 is benign; a 500 must not be silently reported as empty."""
    respx.get(f"{API_BASE}/metadata/naid-availability/naid/1").mock(
        return_value=httpx.Response(500, text="boom")
    )
    out = await call_tool("get_partner_digital_objects", naid="1")
    assert out["error"] == "api_error"
    assert out["status"] == 500


@respx.mock
async def test_partner_objects_404_reports_the_clean_naid(served):
    """The no-match answer carries the NAID it looked up, not the raw input."""
    respx.get(f"{API_BASE}/metadata/naid-availability/naid/1").mock(
        return_value=httpx.Response(404, json={"message": "No matching records"})
    )
    out = await call_tool("get_partner_digital_objects", naid=" 1 ")
    assert out["naid"] == 1
    assert out["catalog_url"] == "https://catalog.archives.gov/id/1"


async def test_partner_objects_rejects_a_non_numeric_naid(served):
    """A bad NAID is caught locally rather than spending quota on it."""
    out = await call_tool("get_partner_digital_objects", naid="not-a-naid")
    assert out["error"] == "invalid_naid"


# --------------------------------------------------------------------------- #
# download_page_image
# --------------------------------------------------------------------------- #
_MEDIA = "https://catalog.archives.gov/medialive/73/7658/54765873/content/1.jpg"
_JPEG = b"\xff\xd8\xff\xe0" + b"scan" * 64


@respx.mock
async def test_page_download_writes_the_file(served, tmp_path):
    """The bytes go to disk; a page scan is not a tool result."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    target = tmp_path / "p1.jpg"
    out = await call_tool("download_page_image", naid="54765873", page=1, destination=str(target))
    assert out["bytes"] == len(_JPEG)
    assert out["page_count"] == 1
    assert target.read_bytes() == _JPEG


@respx.mock
async def test_page_download_sends_no_api_key_to_the_media_host(served, tmp_path):
    """NARA's media host is open. The key must not travel there.

    Verified live 2026-09-23: an objectUrl answers 200 with no x-api-key.
    Sending it would leak the credential for no reason.
    """
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )
    media = respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "p1.jpg")
    )
    assert "x-api-key" not in media.calls.last.request.headers


@respx.mock
async def test_downloading_an_image_does_not_spend_quota(served, tmp_path):
    """A media fetch is not an API call and must not count against the cap."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "a.jpg")
    )
    after_first = served.live_calls
    await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "b.jpg")
    )
    assert served.live_calls == after_first  # second lookup came from cache


@respx.mock
async def test_an_undigitised_record_says_so_rather_than_failing(served, tmp_path):
    """Much of the archive is described but not scanned; that is not an error."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload({"naId": 2173628, "title": "Paper only"}))
    )
    out = await call_tool(
        "download_page_image", naid="2173628", page=1, destination=str(tmp_path / "x.jpg")
    )
    assert out["error"] == "not_digitised"
    assert "reference unit" in out["message"]


@respx.mock
async def test_a_page_beyond_the_record_is_refused_with_the_count(served, tmp_path):
    """Knowing how many pages exist is what the caller needs to correct it."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )
    out = await call_tool(
        "download_page_image", naid="54765873", page=99, destination=str(tmp_path / "x.jpg")
    )
    assert out["error"] == "no_such_page"
    assert "1 page" in out["message"]


async def test_page_download_refuses_a_missing_directory(served, tmp_path):
    """Fail before spending the download on a path that cannot be written."""
    out = await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "nope" / "x.jpg")
    )
    assert out["error"] == "no_such_directory"


@respx.mock
async def test_a_non_jpeg_body_is_not_claimed_as_one(served, tmp_path):
    """An error page saved as .jpg would look like a successful download."""
    respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=b"<html>error</html>"))
    out = await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "x.jpg")
    )
    assert out["format"] == "unknown"


def _one_page_record() -> respx.Route:
    """Mock a record whose single digital object is ``_MEDIA``."""
    return respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(
            search_payload({"naId": 54765873, "digitalObjects": [{"objectUrl": _MEDIA}]})
        )
    )


@respx.mock
async def test_page_download_never_overwrites_an_existing_file(served, tmp_path):
    """A mistaken -- or injected -- path must not be able to destroy a file.

    Tag, comment and transcription text is written by the public and reaches
    the model verbatim, so the destination cannot be trusted to be benign.
    The refusal also comes before any call is spent.
    """
    search = _one_page_record()
    media = respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    precious = tmp_path / "notes.txt"
    precious.write_text("irreplaceable")

    out = await call_tool("download_page_image", naid="54765873", page=1, destination=str(precious))

    assert out["error"] == "destination_exists"
    assert precious.read_text() == "irreplaceable"
    assert not search.called
    assert not media.called


@respx.mock
async def test_a_file_created_mid_download_is_left_alone(served, tmp_path):
    """The existence check and the write are separate moments; close the gap.

    Here the file appears after the check but before the write. Exclusive
    creation refuses it, and the cleanup of a failed download must not
    delete a file this call did not create.
    """
    _one_page_record()
    target = tmp_path / "p1.jpg"

    def appear_then_respond(request):
        target.write_text("written by someone else")
        return httpx.Response(200, content=_JPEG)

    respx.get(_MEDIA).mock(side_effect=appear_then_respond)
    out = await call_tool("download_page_image", naid="54765873", page=1, destination=str(target))

    assert out["error"] == "destination_exists"
    assert target.read_text() == "written by someone else"


@respx.mock
async def test_a_failed_media_fetch_leaves_no_file(served, tmp_path):
    """An error must not leave an empty file that looks like a download."""
    _one_page_record()
    respx.get(_MEDIA).mock(return_value=httpx.Response(404, text="gone"))
    target = tmp_path / "p1.jpg"

    out = await call_tool("download_page_image", naid="54765873", page=1, destination=str(target))

    assert out["error"] == "api_error"
    assert out["status"] == 404
    assert not target.exists()


@respx.mock
async def test_a_transfer_cut_off_midway_leaves_no_partial_file(served, tmp_path):
    """Half a scan saved under the requested name would pass for the page."""
    _one_page_record()

    async def cut_off():
        yield _JPEG[:32]
        raise httpx.ReadError("connection reset")

    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=cut_off()))
    target = tmp_path / "p1.jpg"

    out = await call_tool("download_page_image", naid="54765873", page=1, destination=str(target))

    assert out["error"] == "unexpected"
    assert not target.exists()


@respx.mock
async def test_a_relative_destination_is_reported_as_an_absolute_path(
    served, tmp_path, monkeypatch
):
    """A relative path lands in the server's working directory; say where."""
    _one_page_record()
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    monkeypatch.chdir(tmp_path)

    out = await call_tool("download_page_image", naid="54765873", page=1, destination="p1.jpg")

    assert out["path"] == str((tmp_path / "p1.jpg").resolve())
    assert (tmp_path / "p1.jpg").read_bytes() == _JPEG


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"II*\x00" + b"\x00" * 8, "tiff"),
        (b"%PDF-1.7\n", "pdf"),
    ],
)
@respx.mock
async def test_the_format_is_named_from_the_bytes(served, tmp_path, body, expected):
    """Not every digital object is a JPEG; the caller needs to know what it has."""
    _one_page_record()
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=body))
    out = await call_tool(
        "download_page_image", naid="54765873", page=1, destination=str(tmp_path / "x")
    )
    assert out["format"] == expected


_MEDIA_2 = "https://catalog.archives.gov/medialive/73/7658/54765873/content/2.jpg"
_TWO_PAGES = {
    "naId": 54765873,
    "digitalObjects": [
        {"objectId": "8811", "objectUrl": _MEDIA},
        {"objectId": "8812", "objectUrl": _MEDIA_2},
    ],
}


@respx.mock
async def test_download_by_object_id_fetches_that_page(served, tmp_path):
    """An OCR or transcription entry names an object id; that is enough to fetch it.

    This is the loop the evidence path closes: "this page mentions the
    widow" to "download this page" without a further read of the record.
    """
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(_TWO_PAGES))
    )
    respx.get(_MEDIA_2).mock(return_value=httpx.Response(200, content=_JPEG))
    out = await call_tool(
        "download_page_image",
        naid="54765873",
        object_id="8812",
        destination=str(tmp_path / "p2.jpg"),
    )
    assert (out["page"], out["object_id"], out["source_url"]) == (2, "8812", _MEDIA_2)
    assert "digitalObjects.objectId" in route.calls.last.request.url.params["sourceIncludes"]


@respx.mock
async def test_an_unknown_object_id_is_refused_with_the_count(served, tmp_path):
    """Wrong id, right record: say how many pages there are and where the ids are."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(_TWO_PAGES)))
    out = await call_tool(
        "download_page_image",
        naid="54765873",
        object_id="9999",
        destination=str(tmp_path / "x.jpg"),
    )
    assert out["error"] == "no_such_object"
    assert "2 page" in out["message"]
    assert "get_record_images" in out["message"]


async def test_page_and_object_id_together_are_refused(served, tmp_path):
    """Two ways to name a page that could disagree; take one."""
    out = await call_tool(
        "download_page_image",
        naid="54765873",
        page=1,
        object_id="8812",
        destination=str(tmp_path / "x.jpg"),
    )
    assert out["error"] == "conflicting_page"


async def test_a_non_numeric_object_id_never_reaches_the_api(served, tmp_path):
    """An object id is digits; anything else is a typo, not a lookup."""
    out = await call_tool(
        "download_page_image",
        naid="54765873",
        object_id="page two",
        destination=str(tmp_path / "x.jpg"),
    )
    assert out["error"] == "invalid_object_id"


@respx.mock
async def test_download_defaults_to_the_first_page(served, tmp_path):
    """Neither page nor object_id given: the first page, as before."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(_TWO_PAGES)))
    respx.get(_MEDIA).mock(return_value=httpx.Response(200, content=_JPEG))
    out = await call_tool(
        "download_page_image", naid="54765873", destination=str(tmp_path / "p1.jpg")
    )
    assert (out["page"], out["object_id"]) == (1, "8811")


@respx.mock
async def test_image_list_and_download_share_one_cache_entry(served, tmp_path):
    """Listing the pages and then fetching one must cost one call, not two."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(_TWO_PAGES))
    )
    respx.get(_MEDIA_2).mock(return_value=httpx.Response(200, content=_JPEG))
    listed = await call_tool("get_record_images", naid="54765873")
    await call_tool(
        "download_page_image",
        naid="54765873",
        object_id=listed["images"][1]["object_id"],
        destination=str(tmp_path / "p2.jpg"),
    )
    assert route.call_count == 1
    assert served.cache_hits == 1


# --------------------------------------------------------------------------- #
# Refinements are not search terms
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "refinement",
    [
        {"data_source": "authority"},
        {"available_online": True},
        {"transcriptions_exist": True},
        {"tags_exist": True},
        {"contributions_exist": True},
    ],
)
async def test_a_refinement_alone_is_refused_with_an_explanation(served, refinement):
    """These narrow a search; they cannot be one.

    Verified live 2026-09-23: passing only availableOnline or dataSource
    returns HTTP 400 "No search terms entered". Catching it locally saves a
    call and gives the caller something to act on.
    """
    out = await call_tool("search_records_advanced", **refinement)
    assert out["error"] == "refinements_only"
    assert "cannot be one" in out["message"]


@respx.mock
async def test_a_refinement_with_a_real_term_is_accepted(served, record):
    """The refinement is fine once there is something to refine."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_records_advanced", title="Hall", data_source="authority")
    assert "error" not in out


async def test_no_arguments_at_all_is_a_different_error(served):
    """Nothing given is a different mistake from the wrong thing given."""
    out = await call_tool("search_records_advanced")
    assert out["error"] == "no_criteria"


@respx.mock
async def test_standalone_filters_need_no_companion(served, record):
    """typeOfMaterials and levelOfDescription do work alone; do not block them."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    for args in (
        {"type_of_materials": "Photographs"},
        {"level_of_description": "series"},
        {"recurring_month": "07", "recurring_day": "04"},
        {"control_numbers": "M804"},
        {"congress_number": 55},
        {"title": "Pension File, Joshua Hall", "exact": True},
    ):
        assert "error" not in await call_tool("search_records_advanced", **args)


@respx.mock
async def test_exact_switches_the_identifiers_to_their_is_forms(served, record):
    """title_is matches the whole title; title matches words in it.

    Both forms work standalone, verified 2026-09-28. The plain form must not
    be sent alongside, or the exact one is diluted.
    """
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool(
        "search_records_advanced",
        title="Pension File, Joshua Hall",
        local_identifier="21-A-1",
        microform_publication="M804",
        exact=True,
    )
    params = route.calls.last.request.url.params
    assert params["title_is"] == "Pension File, Joshua Hall"
    assert params["localIdentifier_is"] == "21-A-1"
    assert params["microformPublicationsIdentifier_is"] == "M804"
    for plain in ("title", "localIdentifier", "microformPublicationsIdentifier"):
        assert plain not in params


@respx.mock
async def test_without_exact_the_plain_forms_are_sent(served, record):
    """The default is the words match, and the _is forms stay out."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", title="Hall", microform_publication="M804")
    params = route.calls.last.request.url.params
    assert params["title"] == "Hall"
    assert params["microformPublicationsIdentifier"] == "M804"
    assert "title_is" not in params
    assert "microformPublicationsIdentifier_is" not in params


async def test_exact_alone_is_not_a_search(served):
    """It modifies three parameters; with none of them given there is nothing to do."""
    out = await call_tool("search_records_advanced", exact=True)
    assert out["error"] == "no_criteria"


@respx.mock
async def test_control_numbers_and_congress_map_to_the_api(served, record):
    """Two more standalone terms, verified 2026-09-28."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", control_numbers="M804", congress_number=55)
    params = route.calls.last.request.url.params
    assert params["controlNumbers"] == "M804"
    assert params["congressNumber"] == "55"


async def test_a_congress_before_the_first_is_refused(served):
    """There is no 0th Congress; the API would answer nothing and charge a call."""
    out = await call_tool("search_records_advanced", congress_number=0)
    assert out["error"] == "invalid_congress"


@respx.mock
async def test_include_extracted_text_asks_for_partner_text_too(served, record):
    """NARA's OCR and the partners' text live under separate flags; ask for both."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    await call_tool("search_records_advanced", title="Hall", include_extracted_text=True)
    params = route.calls.last.request.url.params
    assert params["includeExtractedText"] == "true"
    assert params["includeOtherExtractedText"] == "true"

    await call_tool("search_records_advanced", title="Tozier")
    params = route.calls.last.request.url.params
    assert "includeExtractedText" not in params
    assert "includeOtherExtractedText" not in params


# --------------------------------------------------------------------------- #
# search_by_contribution_text
# --------------------------------------------------------------------------- #
@respx.mock
async def test_contribution_text_search_returns_records_not_contributions(served, record):
    """The point of this tool: search what people wrote, get the record back."""
    route = respx.get(f"{API_BASE}/records/search").mock(
        return_value=_ok(search_payload(record, total=2))
    )
    out = await call_tool("search_by_contribution_text", transcription_text='"Rhoda Hall"')
    assert out["total"] == 2
    assert out["records"][0]["naid"] == 54765873
    params = route.calls.last.request.url.params
    assert params["transcriptionContribution"] == '"Rhoda Hall"'


@respx.mock
async def test_each_contribution_kind_maps_to_its_own_parameter(served, record):
    """Tags, comments, transcriptions and OCR are four different indexes."""
    route = respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    for arg, wire in (
        ("tag_text", "tagContribution"),
        ("comment_text", "commentContribution"),
        ("extracted_text", "extractedTextContribution"),
    ):
        await call_tool("search_by_contribution_text", **{arg: "Hall"})
        assert route.calls.last.request.url.params[wire] == "Hall"


async def test_contribution_search_without_text_is_refused(served):
    """Every parameter is a text search; none means nothing to search for."""
    out = await call_tool("search_by_contribution_text")
    assert out["error"] == "no_criteria"


@respx.mock
async def test_contribution_search_carries_the_reading_caution(served, record):
    """A transcription is a volunteer's reading, not the record."""
    respx.get(f"{API_BASE}/records/search").mock(return_value=_ok(search_payload(record)))
    out = await call_tool("search_by_contribution_text", tag_text="Hall")
    assert "not the record" in out["caution"]
