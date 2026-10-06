"""NARA's Access to Archival Databases: parsing, the paced client, the tools.

Every page here was captured from aad.archives.gov on 2026-10-05 (see
``AAD_FIXTURES`` in conftest), so the parsers are tested against what AAD
really serves. Nothing here touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from nara_catalog_mcp import aad, server
from nara_catalog_mcp.aad import (
    AAD_BASE,
    MAX_WAIT_POLLS,
    AadBusy,
    AadError,
    AadLayoutError,
    HostNotAllowed,
    citation,
    is_please_wait,
    parse_record,
    parse_results,
    parse_search_form,
    parse_series_list,
)

from .conftest import aad_client, aad_page, call_tool

FORM = f"{AAD_BASE}fielded-search.jsp"
RESULTS = f"{AAD_BASE}display-partial-records.jsp"
RECORD = f"{AAD_BASE}record-detail.jsp"
SERIES_LIST = f"{AAD_BASE}series-list.jsp"


def _html(name: str, status: int = 200, **headers) -> httpx.Response:
    return httpx.Response(status, text=aad_page(name), headers=headers)


# --------------------------------------------------------------------------- #
# Parsing captured pages
# --------------------------------------------------------------------------- #
def test_the_series_list_gives_each_series_its_files_and_record_group():
    """A series is searched one file at a time, so the files are the point."""
    series = {
        s["series_id"]: s for s in parse_series_list(aad_page("series-list-genealogy"))["series"]
    }
    assert set(series) == {"5218", "5057", "3360"}
    numident = series["5057"]
    assert numident["title"] == "Numerical Identification Files (NUMIDENT)"
    assert numident["record_group"] == (
        "Record Group 47: Records of the Social Security Administration"
    )
    assert numident["dates"] == "created 1936 - 2007, documenting the period 1936 - 2007"
    assert numident["rows"] == 146_870_508
    assert len(numident["files"]) == 24
    assert numident["files"][0] == {
        "file_id": "3446",
        "title": "Application (SS-5) Files, 1936 - 2007 (Last Names A through B)",
        "rows": 8_956_563,
    }
    assert series["5218"]["record_group"] == (
        "Collection CGIC: Castle Garden Immigration Center Collection"
    )


def test_a_page_without_series_is_a_layout_error_not_an_empty_list():
    """An empty list would read as "AAD holds nothing here"."""
    with pytest.raises(AadLayoutError):
        parse_series_list(aad_page("not-found"))


def test_the_search_form_says_how_each_field_can_be_searched():
    """Text, number and coded fields take different parameters."""
    form = parse_search_form(aad_page("fielded-search-3433"))
    kinds = {f["name"]: f["kind"] for f in form["fields"]}
    assert kinds["LAST NAME"] == "text"
    assert kinds["DATE OF BIRTH (YEAR)"] == "number"
    assert kinds["STATE OR FOREIGN COUNTRY OF BIRTH"] == "coded"
    last = next(f for f in form["fields"] if f["name"] == "LAST NAME")
    assert (last["column"], last["nfo"]) == ("31508", "V,20,1900")
    assert form["columns"] == "31504,31432,31460,31508,31445,31446,31447,31491,31466"
    assert form["file"] == "Application (SS-5) Files, 1936 - 2007 (Last Names O through R)"
    assert form["series_id"] == "5057"
    assert form["notice"].startswith("These files do not contain records of all")


def test_a_results_page_gives_rows_with_their_record_ids():
    """The record id is what aad_get_record takes."""
    out = parse_results(aad_page("partial-3259-beilin-israel"))
    assert (out["found"], out["file_rows"], out["page"], out["pages"]) == (1, 835_984, 1, 1)
    assert out["records"] == [
        {"record_id": "470718", "LAST NAME": "BEILIN", "FIRST NAME": "ISRAEL"}
    ]
    assert out["series"] == "Ship Passenger Records"
    assert "Manifest Header Records" in out["notice"]


def test_shaded_rows_are_read_and_empty_cells_left_out():
    """AAD shades every other row with a bgcolor attribute."""
    out = parse_results(aad_page("partial-3433-presley"))
    assert [r["record_id"] for r in out["records"]] == ["4117667", "4117668", "4118111"]
    shaded = out["records"][1]
    assert shaded["MIDDLE NAME"] == "D"
    assert "PLACE OF BIRTH CITY" not in shaded
    assert out["records"][2]["DATE OF BIRTH (YEAR)"] == "1934"


def test_a_free_text_match_outside_the_columns_says_where_it_matched():
    """BERLIN matched a departure town, not a name; the row has to say so."""
    out = parse_results(aad_page("partial-3259-berlin-page2"))
    assert (out["found"], out["page"], out["pages"]) == (725, 2, 15)
    first = out["records"][0]
    assert first["LAST NAME"] == "FREIWUSC"
    assert first["matched_in"].startswith("CITY/TOWN/VILLAGE OF DEPARTURE: BERLIN")


def test_no_match_is_an_answer():
    """Zero is a result; it must not be mistaken for a broken page."""
    out = parse_results(aad_page("partial-3259-none"))
    assert (out["found"], out["records"]) == (0, [])
    assert out["file_rows"] == 835_984


def test_a_page_without_a_match_count_is_a_layout_error():
    """Better to say the page was unreadable than to report no matches."""
    with pytest.raises(AadLayoutError):
        parse_results(aad_page("record-3259-470718"))


def test_a_record_gives_values_and_the_meaning_of_codes():
    """A coded value means nothing without its meaning."""
    out = parse_record(aad_page("record-3259-470718"))
    fields = {f["field"]: f for f in out["fields"]}
    assert fields["FIRST NAME"] == {"field": "FIRST NAME", "value": "ISRAEL"}
    assert fields["OCCUPATION"] == {
        "field": "OCCUPATION",
        "value": "316",
        "meaning": "CHILD, YOUNGSTER",
    }
    assert fields["COUNTRY OF BIRTH"]["meaning"] == "RUSSIA"
    # Empty fields are left out, not reported blank.
    assert "MARITAL STATUS" not in fields
    assert out["record_group"] == "Collection CGIC: Castle Garden Immigration Center Collection"


def test_a_numident_row_names_the_parents():
    """The reason to read the NUMIDENT: parents and birthplace, which the SSDI omits."""
    fields = {f["field"]: f for f in parse_record(aad_page("record-3433-4118111"))["fields"]}
    assert fields["MOTHER'S FIRST NAME"]["value"] == "GLADYS"
    assert fields["MOTHER'S LAST NAME"]["value"] == "SMITH"
    assert fields["FATHER'S FIRST NAME"]["value"] == "VERNON"
    assert fields["PLACE OF BIRTH CITY"]["value"] == "TUPELO LEE"
    # The row says 1934. He was born in 1935: a row is not the record.
    assert fields["DATE OF BIRTH (YEAR)"]["value"] == "1934"


def test_a_record_id_aad_does_not_hold_comes_back_empty():
    """AAD answers 200 with every field blank, not 404."""
    assert parse_record(aad_page("record-3259-missing"))["fields"] == []


def test_the_please_wait_page_is_recognised():
    """It comes first on every search, and must never be parsed as a result."""
    assert is_please_wait(aad_page("please-wait"))
    assert not is_please_wait(aad_page("partial-3259-none"))


def test_a_citation_names_file_series_group_and_retrieval_date():
    """NARA's own guidance: file, series, record group, and the date retrieved."""
    record = parse_record(aad_page("record-3433-4118111"))
    record.update(url=aad.record_url("3433", "4118111"), retrieved="2026-10-05")
    text = citation(record)
    assert text.startswith("Application (SS-5) Files, 1936 - 2007 (Last Names O through R); ")
    assert "Numerical Identification Files (NUMIDENT), created 1936 - 2007" in text
    assert "Record Group 47: Records of the Social Security Administration" in text
    assert text.endswith(
        "[Retrieved from the Access to Archival Databases (AAD), "
        "https://aad.archives.gov/aad/record-detail.jsp?dt=3433&rid=4118111, 2026-10-05.]"
    )


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
def _parse_text(page: str) -> dict:
    return {"text": page[:20]}


@respx.mock
async def test_the_client_names_itself_and_sends_no_key(tmp_path):
    """AAD needs no key; the Catalog's must never travel there."""
    route = respx.get(RECORD).mock(return_value=_html("record-3259-470718"))
    client = aad_client(tmp_path)
    await client.read("record-detail.jsp", {"dt": "3259", "rid": "470718"}, parse_record)
    request = route.calls.last.request
    agent = request.headers["user-agent"]
    assert agent.startswith("Mozilla/5.0 (compatible; nara-catalog-mcp/")
    assert "+https://github.com/ianderso/nara-catalog-mcp" in agent
    assert "x-api-key" not in request.headers


@respx.mock
async def test_the_client_refuses_any_other_host(tmp_path):
    """A model-chosen URL must not turn this client into a general fetcher."""
    route = respx.route().mock(return_value=httpx.Response(200, text="x"))
    client = aad_client(tmp_path)
    with pytest.raises(HostNotAllowed):
        await client._http.get("https://example.com/")
    assert not route.called


@respx.mock
async def test_please_wait_is_asked_again_in_the_same_session(tmp_path):
    """AAD runs the search behind a self-refreshing page tied to the session."""
    route = respx.get(RESULTS).mock(
        side_effect=[
            _html("please-wait", **{"set-cookie": "JSESSIONID=abc; Path=/aad"}),
            _html("partial-3259-beilin-israel"),
        ]
    )
    client = aad_client(tmp_path)
    out = await client.read("display-partial-records.jsp", {"dt": "3259"}, parse_results)
    assert out["found"] == 1
    assert route.call_count == 2
    assert "JSESSIONID=abc" in route.calls.last.request.headers["cookie"]
    assert client.live_calls == 2


@respx.mock
async def test_a_search_that_never_finishes_is_reported_not_parsed(tmp_path):
    """After the last poll the caller hears AAD is busy, not that nothing matched."""
    route = respx.get(RESULTS).mock(return_value=_html("please-wait"))
    client = aad_client(tmp_path)
    with pytest.raises(AadBusy):
        await client.read("display-partial-records.jsp", {"dt": "3259"}, parse_results)
    assert route.call_count == MAX_WAIT_POLLS + 1


@respx.mock
async def test_answers_are_cached_with_their_retrieval_date_until_they_expire(tmp_path):
    """The cache date is the retrieval date a citation must give."""
    now = [datetime(2026, 10, 5, 12, tzinfo=UTC)]
    route = respx.get(RECORD).mock(return_value=_html("record-3259-470718"))
    client = aad_client(tmp_path, now=lambda: now[0])
    params = {"dt": "3259", "rid": "470718"}

    first = await client.read("record-detail.jsp", params, parse_record)
    now[0] += timedelta(days=3)
    second = await client.read("record-detail.jsp", params, parse_record)
    assert route.call_count == 1
    assert first["retrieved"] == second["retrieved"] == "2026-10-05"
    assert first["url"] == "https://aad.archives.gov/aad/record-detail.jsp?dt=3259&rid=470718"

    now[0] += timedelta(days=5)
    third = await client.read("record-detail.jsp", params, parse_record)
    assert route.call_count == 2
    assert third["retrieved"] == "2026-10-13"


@respx.mock
async def test_refresh_fetches_again(tmp_path):
    """A reload can change a row; refresh is how a caller sees the change."""
    route = respx.get(RECORD).mock(return_value=_html("record-3259-470718"))
    client = aad_client(tmp_path)
    params = {"dt": "3259", "rid": "470718"}
    await client.read("record-detail.jsp", params, parse_record)
    await client.read("record-detail.jsp", params, parse_record, refresh=True)
    assert route.call_count == 2
    assert (client.live_calls, client.cache_hits) == (2, 0)


@respx.mock
async def test_a_page_that_fails_to_parse_is_never_cached(tmp_path):
    """Otherwise one odd page would answer for a week."""
    route = respx.get(RESULTS).mock(
        side_effect=[_html("record-3259-470718"), _html("partial-3259-none")]
    )
    client = aad_client(tmp_path)
    with pytest.raises(AadLayoutError):
        await client.read("display-partial-records.jsp", {"dt": "3259"}, parse_results)
    out = await client.read("display-partial-records.jsp", {"dt": "3259"}, parse_results)
    assert out["found"] == 0
    assert route.call_count == 2


@respx.mock
async def test_failures_carry_the_status_and_what_aad_said(tmp_path):
    """A 404 page, a redirect and a dropped connection are each named."""
    client = aad_client(tmp_path)
    respx.get(FORM).mock(return_value=_html("not-found", status=404))
    with pytest.raises(AadError) as exc:
        await client.read("fielded-search.jsp", {"dt": "999999"}, parse_search_form)
    assert exc.value.status == 404
    assert "not available" in exc.value.detail

    respx.get(SERIES_LIST).mock(
        return_value=httpx.Response(302, headers={"location": "http://aad.archives.gov/aad/"})
    )
    with pytest.raises(AadError) as exc:
        await client.read("series-list.jsp", {}, parse_series_list)
    assert exc.value.status == 302
    assert "redirected" in exc.value.detail

    respx.get(RECORD).mock(side_effect=httpx.ConnectError("no route to host"))
    with pytest.raises(AadError) as exc:
        await client.read("record-detail.jsp", {}, parse_record)
    assert exc.value.status == 0


@respx.mock
async def test_requests_are_spaced_by_the_interval(tmp_path):
    """One request at a time, and a courtesy gap between the starts."""
    respx.get(RECORD).mock(return_value=_html("record-3259-470718"))
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    client = aad.AadClient(tmp_path, interval=2.0, clock=lambda: 100.0, sleep=sleep)
    for rid in ("1", "2", "3"):
        await client.read("record-detail.jsp", {"dt": "3259", "rid": rid}, parse_record)
    assert waits == [2.0, 2.0]


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #
@respx.mock
async def test_list_series_reads_the_genealogy_category_by_default(served):
    """AAD's Genealogy/Personal History group is the useful default."""
    route = respx.get(SERIES_LIST).mock(return_value=_html("series-list-genealogy"))
    out = await call_tool("aad_list_series")
    assert route.calls.last.request.url.params["cat"] == "GP21,22,23,24,44"
    assert [s["series_id"] for s in out["series"]] == ["5218", "5057", "3360"]
    assert out["source_url"].startswith(SERIES_LIST)


@respx.mock
async def test_a_series_with_many_files_is_counted_until_asked_for(served, monkeypatch):
    """One securities series has 138 files; the list should not carry them all."""
    monkeypatch.setattr(server, "MAX_FILES_LISTED", 4)
    respx.get(SERIES_LIST).mock(return_value=_html("series-list-genealogy"))
    listed = await call_tool("aad_list_series")
    cgic = next(s for s in listed["series"] if s["series_id"] == "5218")
    assert cgic["file_count"] == 5
    assert "files" not in cgic
    assert "series_id='5218'" in cgic["note"]

    one = await call_tool("aad_list_series", series_id="5218")
    assert one["series_count"] == 1
    assert len(one["series"][0]["files"]) == 5


@respx.mock
async def test_list_series_refuses_an_id_it_does_not_hold(served):
    """A wrong category is the likely cause; say so."""
    respx.get(SERIES_LIST).mock(return_value=_html("series-list-genealogy"))
    assert (await call_tool("aad_list_series", series_id="625"))["error"] == "not_in_category"
    assert (await call_tool("aad_list_series", series_id="s=5057"))["error"] == "invalid_series_id"


@respx.mock
async def test_search_sends_each_field_in_the_form_aad_expects(served):
    """Text fields match all words; a number range uses AAD's between operator."""
    respx.get(FORM).mock(return_value=_html("fielded-search-3433"))
    route = respx.get(RESULTS).mock(return_value=_html("partial-3433-presley"))
    out = await call_tool(
        "aad_search",
        file_id="3433",
        fields={"last name": "PRESLEY", "FIRST NAME": "ELVIS", "DATE OF BIRTH (YEAR)": "1880-1935"},
    )
    params = route.calls.last.request.url.params
    assert (params["txt_31508"], params["op_31508"], params["nfo_31508"]) == (
        "PRESLEY",
        "0",
        "V,20,1900",
    )
    assert params["op_31447"] == "8"
    assert params.get_list("txt_31447") == ["1880", "1935"]
    assert (params["dt"], params["rpp"], params["pg"]) == ("3433", "50", "1")
    assert params["sc"] == "31504,31432,31460,31508,31445,31446,31447,31491,31466"
    assert out["found"] == 3
    assert [r["record_id"] for r in out["records"]] == ["4117667", "4117668", "4118111"]
    assert "STATE OR FOREIGN COUNTRY OF BIRTH (coded: use query)" in out["searchable_fields"]
    assert "DATE OF BIRTH (YEAR) (number)" in out["searchable_fields"]
    assert "verified death" in out["numident_note"]
    assert "not the record" in out["caution"]


@respx.mock
async def test_a_single_number_is_an_equals_search(served):
    respx.get(FORM).mock(return_value=_html("fielded-search-3433"))
    route = respx.get(RESULTS).mock(return_value=_html("partial-3433-presley"))
    await call_tool("aad_search", file_id="3433", fields={"DATE OF BIRTH (YEAR)": 1934})
    params = route.calls.last.request.url.params
    assert (params["op_31447"], params["txt_31447"]) == ("3", "1934")


@respx.mock
async def test_an_unknown_field_is_refused_with_the_fields_there_are(served):
    """A guessed field name must not be silently dropped from the search."""
    respx.get(FORM).mock(return_value=_html("fielded-search-3433"))
    route = respx.get(RESULTS).mock(return_value=_html("partial-3433-presley"))
    out = await call_tool("aad_search", file_id="3433", fields={"SURNAME": "PRESLEY"})
    assert out["error"] == "unknown_field"
    assert "LAST NAME" in out["message"]
    assert not route.called


@respx.mock
async def test_a_coded_field_points_to_query(served):
    """A state is searched by a code the caller cannot know; free text works."""
    respx.get(FORM).mock(return_value=_html("fielded-search-3433"))
    out = await call_tool(
        "aad_search", file_id="3433", fields={"STATE OR FOREIGN COUNTRY OF BIRTH": "MS"}
    )
    assert out["error"] == "coded_field"
    assert "query" in out["message"]


@respx.mock
async def test_a_number_field_refuses_words(served):
    respx.get(FORM).mock(return_value=_html("fielded-search-3433"))
    out = await call_tool("aad_search", file_id="3433", fields={"DATE OF BIRTH (YEAR)": "c. 1934"})
    assert out["error"] == "invalid_number"


async def test_an_empty_search_is_refused_before_any_request(served):
    """It would list the whole file; respx would fail the test on any request."""
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(500))
        out = await call_tool("aad_search", file_id="3433", fields={"LAST NAME": "  "})
        assert out["error"] == "no_criteria"
        assert not route.called


@respx.mock
async def test_search_pages_and_says_what_comes_next(served):
    respx.get(FORM).mock(return_value=_html("fielded-search-3259"))
    route = respx.get(RESULTS).mock(return_value=_html("partial-3259-berlin-page2"))
    out = await call_tool("aad_search", file_id="3259", query="BERLIN", page=2)
    params = route.calls.last.request.url.params
    assert (params["q"], params["pg"]) == ("BERLIN", "2")
    assert (out["page"], out["pages"], out["next_page"]) == (2, 15, 3)
    assert "numident_note" not in out


@respx.mock
async def test_get_record_returns_fields_and_a_citation(served):
    """The citation is what lets a reader find the row again."""
    respx.get(RECORD).mock(return_value=_html("record-3259-470718"))
    out = await call_tool("aad_get_record", file_id="3259", record_id="470718")
    assert out["record_url"] == "https://aad.archives.gov/aad/record-detail.jsp?dt=3259&rid=470718"
    assert out["citation"].startswith(
        "Records of Passengers from the Russian Empire, Austria-Hungary, the Netherlands"
    )
    assert out["retrieved"] in out["citation"]
    assert {"field": "AGE", "value": "5", "meaning": "age 5"} in out["fields"]
    assert out["series_id"] == "5218"


@respx.mock
async def test_get_record_reports_an_id_aad_does_not_hold(served):
    """AAD answers a blank record; the tool says not found, and why ids go stale."""
    respx.get(RECORD).mock(return_value=_html("record-3259-missing"))
    out = await call_tool("aad_get_record", file_id="3259", record_id="999999999")
    assert out["error"] == "not_found"
    assert "reloads" in out["message"]


@respx.mock
async def test_get_record_refresh_re_reads_aad(served):
    route = respx.get(RECORD).mock(return_value=_html("record-3433-4118111"))
    await call_tool("aad_get_record", file_id="3433", record_id="4118111")
    out = await call_tool("aad_get_record", file_id="3433", record_id="4118111")
    assert route.call_count == 1
    await call_tool("aad_get_record", file_id="3433", record_id="4118111", refresh=True)
    assert route.call_count == 2
    assert "refresh" not in route.calls.last.request.url.params
    assert "SSA" in out["numident_note"]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"file_id": "dt=3259", "record_id": "1"}, "invalid_file_id"),
        ({"file_id": "3259", "record_id": "rid 1"}, "invalid_record_id"),
    ],
)
async def test_ids_are_digits_or_refused(served, args, error):
    assert (await call_tool("aad_get_record", **args))["error"] == error


@respx.mock
async def test_an_unreadable_page_is_not_an_empty_result(served):
    """If AAD changes its pages, the caller must hear that, not "no matches"."""
    respx.get(FORM).mock(return_value=_html("fielded-search-3259"))
    respx.get(RESULTS).mock(return_value=_html("record-3259-470718"))
    out = await call_tool("aad_search", file_id="3259", query="BEILIN")
    assert out["error"] == "aad_layout_changed"
    assert "not an empty result" in out["message"]


@respx.mock
async def test_a_refusal_by_aad_is_explained(served):
    """AAD's firewall answers 403 to clients it does not admit."""
    respx.get(SERIES_LIST).mock(return_value=httpx.Response(403, text="<h1>403 Forbidden</h1>"))
    out = await call_tool("aad_list_series")
    assert (out["error"], out["status"]) == ("aad_error", 403)
    assert "firewall" in out["meaning"]


@respx.mock
async def test_the_aad_tools_need_no_catalog_key(tmp_path, monkeypatch):
    """AAD is open; a user without a Catalog key can still search it."""
    monkeypatch.delenv("NARA_API_KEY", raising=False)
    monkeypatch.setenv("NARA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server.state, "config", None)
    monkeypatch.setattr(server.state, "client", None)
    monkeypatch.setattr(server.state, "aad", None)
    respx.get(SERIES_LIST).mock(return_value=_html("series-list-genealogy"))
    out = await call_tool("aad_list_series")
    assert out["series_count"] == 3
    assert (tmp_path / "cache" / "aad").is_dir()
    # The Catalog tools still ask for the key.
    assert (await call_tool("get_record", naid="1"))["error"] == "not_configured"
