"""Contract tests over the registered MCP tool surface.

These assert properties of the tools *as a client sees them*: their names,
their JSON schema, and the size of the description block shipped on every
session. The rest of the suite exercises the client and the shaping, which
means a ``Field`` typo or a dropped docstring could change the published
contract without failing a single test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import respx

from nara_catalog_mcp.client import API_BASE
from nara_catalog_mcp.server import mcp

from .conftest import assert_reached_body, call_tool, valid_args

SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_schema.json"
README = Path(__file__).parent.parent / "README.md"

#: Ceiling on the combined tool descriptions, which are sent to the model on
#: every session before any work happens. The surface is 18 tools averaging
#: ~450 characters; raise this deliberately, not by accident.
DESCRIPTION_BUDGET = 8_500

#: Tools that return somebody else's reading of a document -- machine OCR or a
#: volunteer's typing. Each must tell the model, in the description it acts on,
#: that this is not the evidence.
SECOND_HAND_READING_TOOLS = (
    "get_extracted_text",
    "get_transcriptions",
    "get_tags",
    "search_extracted_text",
    "search_transcriptions",
    "search_tags",
)


async def _tools() -> list:
    return sorted(await mcp.list_tools(), key=lambda t: t.name)


def _params(tool) -> dict:
    return (tool.input_schema or {}).get("properties", {}) or {}


def _documented_in_readme() -> set[str]:
    doc = README.read_text()
    documented = set(re.findall(r"\| `([a-z_]+)`", doc))
    for pair in re.findall(r"`([a-z_]+)` / `([a-z_]+)`", doc):
        documented.update(pair)
    return documented


async def test_every_tool_has_a_description():
    """A tool with no description is invisible to the model choosing tools."""
    missing = [t.name for t in await _tools() if not (t.description or "").strip()]
    assert missing == []


async def test_every_parameter_has_a_description():
    """An undescribed parameter gets guessed at, and a guess spends quota."""
    undocumented = [
        f"{t.name}.{name}"
        for t in await _tools()
        for name, spec in _params(t).items()
        if not (spec.get("description") or "").strip()
    ]
    assert undocumented == []


async def test_no_parameter_leaks_a_python_repr():
    """A FieldInfo or PydanticUndefined in a schema means a broken default."""
    leaked = [
        f"{t.name}.{name}"
        for t in await _tools()
        for name, spec in _params(t).items()
        if "FieldInfo" in json.dumps(spec) or "PydanticUndefined" in json.dumps(spec)
    ]
    assert leaked == []


async def test_tool_names_and_parameters_match_the_snapshot():
    """Renaming a tool or a parameter breaks callers; make it a visible diff.

    Regenerate deliberately with::

        uv run python -m tests.regen_tool_snapshot
    """
    current = {t.name: sorted(_params(t)) for t in await _tools()}
    expected = json.loads(SNAPSHOT.read_text())
    assert current == expected


async def test_every_registered_tool_appears_in_the_readme():
    """A tool the README does not list is a tool nobody will find."""
    missing = sorted({t.name for t in await _tools()} - _documented_in_readme())
    assert missing == [], f"tools missing from the README table: {missing}"


async def test_the_readme_states_the_tool_count_it_actually_has():
    """The count is the first thing a reader checks against the table under it."""
    match = re.search(r"publishes (\d+) tools", README.read_text())
    assert match, "README no longer states how many tools there are"
    assert int(match.group(1)) == len(await _tools())


async def test_the_readme_does_not_document_a_tool_that_was_removed():
    """A table entry for a tool that no longer exists is worse than none."""
    names = {t.name for t in await _tools()}
    stale = sorted(
        n
        for n in _documented_in_readme() - names
        if n.startswith(("search_", "get_", "browse_", "list_", "api_"))
    )
    assert stale == [], f"README documents tools that do not exist: {stale}"


async def test_description_block_stays_within_budget():
    """Every byte here is spent on every session, before any work happens."""
    total = sum(len(t.description or "") for t in await _tools())
    assert total <= DESCRIPTION_BUDGET, (
        f"tool descriptions total {total} chars, over the {DESCRIPTION_BUDGET} budget"
    )


async def test_required_parameters_have_no_default():
    """A required parameter with a default is a contradiction in the schema."""
    bad = []
    for tool in await _tools():
        schema = tool.input_schema or {}
        for name in schema.get("required", []) or []:
            if "default" in (schema.get("properties", {}).get(name) or {}):
                bad.append(f"{tool.name}.{name}")
    assert bad == []


@pytest.mark.parametrize("tool_name", SECOND_HAND_READING_TOOLS)
async def test_second_hand_readings_are_described_as_leads(tool_name):
    """OCR and citizen transcription must not read as though they were the page.

    The model acts on the description, so this is where the distinction has to
    live: the server exists to find the image worth reading, not to replace
    reading it with a stranger's typing.
    """
    tool = next(t for t in await _tools() if t.name == tool_name)
    description = (tool.description or "").lower()
    assert "not evidence" in description, f"{tool_name} does not say it is not evidence"
    assert "image" in description, f"{tool_name} does not point back at the image"


async def test_no_tool_offers_to_write_to_the_catalog():
    """The Catalog's POST and PUT routes are out of scope, by decision.

    A contribution publishes under the operator's account and cannot be
    withdrawn from here, so nothing in the schema may invite one.
    """
    for tool in await _tools():
        blob = json.dumps({"d": tool.description, "s": tool.input_schema}).lower()
        for verb in ("post a ", "submit a ", "contribute a ", "upload "):
            assert verb not in blob, f"{tool.name} hints at writing: {verb}"


@respx.mock
async def test_no_tool_raises_when_the_api_fails(served):
    """A tool that raises kills the call; every failure must be an envelope.

    This sweeps the whole surface, so a tool added without its try/except
    fails here rather than in front of a user.

    Arguments come from ``valid_args`` and every result passes through
    ``assert_reached_body``, so a tool that refuses the sweep's input before
    doing any work fails here rather than passing without being tested.
    """
    respx.route(host="catalog.archives.gov").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    for tool in await _tools():
        out = await call_tool(tool.name, **valid_args(tool))

        assert isinstance(out, dict), f"{tool.name} did not return an object"
        assert_reached_body(tool.name, out)
        if tool.name != "api_budget":
            assert out.get("error") == "api_error", (
                f"{tool.name} returned {out} instead of an api_error envelope"
            )
            assert out.get("status") == 500


@respx.mock
async def test_no_tool_is_reachable_without_the_api_key_header(served, record):
    """Every route must carry x-api-key; an unauthenticated call just 401s."""
    from .conftest import search_payload

    route = respx.route(host="catalog.archives.gov").mock(
        return_value=httpx.Response(200, json=search_payload(record))
    )
    await call_tool("search_records", title="Hall")
    await call_tool("get_tags", naid="54765873")
    await call_tool("browse_children", parent_naid="300020")
    assert route.call_count == 3
    for call in route.calls:
        assert call.request.headers["x-api-key"] == "k"
    assert str(route.calls[0].request.url).startswith(API_BASE)


async def test_no_schema_carries_an_auto_generated_title():
    """Titles are roughly a tenth of the published block and say nothing.

    Pydantic derives one from each field's own name. ``compact_schemas``
    strips them at import; this asserts none creeps back in through a new
    tool.
    """

    def schema_titles(node, path=""):
        """Yield every ``title`` *keyword*, ignoring parameters named title."""
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):
                yield f"{path}.title"
            for keyword, value in node.items():
                if keyword in ("properties", "$defs", "definitions"):
                    if isinstance(value, dict):
                        for name, sub in value.items():
                            yield from schema_titles(sub, f"{path}.{keyword}.{name}")
                elif keyword != "title":
                    yield from schema_titles(value, f"{path}.{keyword}")
        elif isinstance(node, list):
            for item in node:
                yield from schema_titles(item, path)

    found = [
        t for tool in await _tools() for t in schema_titles(tool.input_schema or {}, tool.name)
    ]
    assert found == []


async def test_compaction_actually_removed_something():
    """A no-op optimisation should not be left in place looking like one."""
    from nara_catalog_mcp.server import SCHEMA_CHARS_SAVED

    assert SCHEMA_CHARS_SAVED > 500


#: The one tool with a local side effect. Everything else only reads.
WRITES_LOCALLY = {"download_page_image"}


async def test_every_tool_declares_its_annotations():
    """A client decides what needs approval from these; absent means unknown."""
    missing = [t.name for t in await _tools() if t.annotations is None]
    assert missing == []


async def test_every_tool_but_the_download_is_marked_read_only():
    """The read-only claim in the README is only useful if the client sees it."""
    wrong = [
        t.name
        for t in await _tools()
        if t.annotations.read_only_hint is not (t.name not in WRITES_LOCALLY)
    ]
    assert wrong == []


async def test_the_download_is_marked_as_writing_but_not_destroying():
    """It creates files, so it is not read-only; it never overwrites one."""
    tool = next(t for t in await _tools() if t.name == "download_page_image")
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False


async def test_the_server_reports_its_own_version():
    """An empty serverInfo.version leaves a bug report unable to say which."""
    from nara_catalog_mcp import __version__

    assert mcp.version == __version__
    assert __version__


async def test_compaction_is_idempotent():
    """It runs at import; running it again must not corrupt the schemas."""
    from nara_catalog_mcp.server import compact_schemas

    assert compact_schemas() == 0


# --------------------------------------------------------------------------- #
# Unknown parameters are refused, not dropped
# --------------------------------------------------------------------------- #
@respx.mock
async def test_every_tool_refuses_a_parameter_it_does_not_define():
    """A misnamed argument must fail loudly, on every tool.

    The SDK's default is to ignore it, so the call answers as if that filter
    had never been given: a plausible wrong answer rather than an error. The
    same defect was reported from real use in both sibling servers.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    # Should the refusal regress, the tools run for real; keep them offline.
    respx.route().mock(return_value=httpx.Response(200, json={}))
    accepted = []
    for tool in await _tools():
        args = valid_args(tool, not_a_parameter="x")
        try:
            await mcp.call_tool(tool.name, args)
        except ToolError as exc:
            assert "not_a_parameter" in str(exc), tool.name
            continue
        accepted.append(tool.name)
    assert accepted == [], f"accepted an undefined parameter: {accepted}"


@respx.mock
async def test_an_advanced_filter_on_the_plain_search_is_refused():
    """The trap this server had: ``start_date`` belongs to the advanced search.

    ``search_records`` accepted it and returned every date, which reads as a
    filtered answer. The refusal names what the plain search does take.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    respx.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("search_records", {"title": "Hall", "start_date": "1900"})
    message = str(exc.value)
    assert "search_records has no parameter 'start_date'" in message
    assert "It takes: limit, page, query, title." in message


async def test_every_published_schema_forbids_additional_properties():
    """A client that validates against the schema can refuse before sending."""
    loose = [
        t.name
        for t in await _tools()
        if (t.input_schema or {}).get("additionalProperties") is not False
    ]
    assert loose == []


async def test_refusing_unknown_arguments_is_idempotent():
    """It runs at import; running it again must not wrap a model twice."""
    from nara_catalog_mcp.server import refuse_unknown_arguments

    assert refuse_unknown_arguments() == 0
