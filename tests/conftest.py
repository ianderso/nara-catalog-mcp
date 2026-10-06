"""Shared fixtures. Every test runs against a mocked API; no key is needed.

The record fixtures are a small corpus rather than one specimen: a digitised
item, an undigitised description, a series with children, a record carrying
contributions, and one whose nesting is deliberately broken. Catalog records
are uneven, and the shaping code is where that bites.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from nara_catalog_mcp import server
from nara_catalog_mcp.aad import AadClient
from nara_catalog_mcp.client import BUDGET_FILE, NaraClient
from nara_catalog_mcp.config import Config


def search_payload(*records: dict, total: int | None = None, sort: list | None = None) -> dict:
    """Wrap records in the envelope the Catalog search endpoint returns.

    Parameters
    ----------
    *records
        Records to place in ``body.hits.hits[]._source.record``.
    total : int, optional
        Reported match count. Defaults to the number of records given.
    sort : list, optional
        Sort key attached to the last hit, which is what ``searchAfter``
        paging feeds back as its cursor.
    """
    hits = [{"_source": {"record": r}} for r in records]
    if sort is not None and hits:
        hits[-1]["sort"] = sort
    return {
        "body": {
            "hits": {
                "total": {"value": total if total is not None else len(records)},
                "hits": hits,
            }
        }
    }


def contribution_payload(*items: dict, wrapped: bool = False) -> dict:
    """Wrap contributions in the envelope the contribution routes return.

    Parameters
    ----------
    *items
        Contribution objects.
    wrapped : bool, optional
        Nest each object under ``_source.record`` as the description routes
        do. Both shapes occur, and both must unwrap.
    """
    hits = [{"_source": ({"record": i} if wrapped else i)} for i in items]
    return {"body": {"hits": {"total": {"value": len(items)}, "hits": hits}}}


@pytest.fixture
def record() -> dict:
    """A digitised pension file, shaped like a real Catalog record."""
    return {
        "naId": 54765873,
        "title": "Revolutionary War Pension File W.17050, Joshua Hall",
        "levelOfDescription": "item",
        "recordType": "description",
        "ancestors": [
            {
                "levelOfDescription": "recordGroup",
                "title": "Records of the Department of Veterans Affairs",
            },
            {"levelOfDescription": "series", "title": "Case Files of Pension Applications"},
        ],
        "physicalOccurrences": [{"referenceUnits": [{"name": "National Archives Building"}]}],
        "digitalObjects": [
            {
                "objectId": "8811",
                "objectUrl": "https://catalog.archives.gov/OpaAPI/media/1.jpg",
            },
            {
                "objectId": "8812",
                "objectUrl": "https://catalog.archives.gov/OpaAPI/media/2.jpg",
            },
        ],
        "scopeAndContentNote": "Widow's pension application.",
        "coverageStartDate": "1837",
        "coverageEndDate": "1853",
        "onlineResources": [{"url": "https://example.gov/finding-aid"}],
    }


@pytest.fixture
def undigitised_record() -> dict:
    """A description whose pages are paper only: no digital objects at all."""
    return {
        "naId": 2173628,
        "title": "Compiled Service Record, Silas Hall, 4th Regiment",
        "levelOfDescription": "fileUnit",
        "recordType": "description",
        "ancestors": [
            {
                "levelOfDescription": "recordGroup",
                "title": "War Department Collection of Revolutionary War Records",
            },
        ],
        "physicalOccurrences": [{"referenceUnits": [{"name": "National Archives at St. Louis"}]}],
        "scopeAndContentNote": "Not digitised; consult the reading room.",
        "coverageStartDate": "1775",
        "coverageEndDate": "1783",
    }


@pytest.fixture
def series_record() -> dict:
    """A series: the node a caller browses down from."""
    return {
        "naId": 300020,
        "title": "Case Files of Pension Applications, 1800-1900",
        "levelOfDescription": "series",
        "recordType": "description",
        "ancestors": [
            {
                "levelOfDescription": "recordGroup",
                "title": "Records of the Department of Veterans Affairs",
            },
        ],
        "physicalOccurrences": [{"referenceUnits": [{"name": "National Archives Building"}]}],
    }


@pytest.fixture
def child_records() -> list[dict]:
    """Two file units sitting directly under the series."""
    return [
        {
            "naId": 300021,
            "title": "Pension File, Abner Hall",
            "levelOfDescription": "fileUnit",
            "ancestors": [{"levelOfDescription": "series", "title": "Case Files"}],
        },
        {
            "naId": 300022,
            "title": "Pension File, Joshua Hall",
            "levelOfDescription": "fileUnit",
            "ancestors": [{"levelOfDescription": "series", "title": "Case Files"}],
        },
    ]


def _land_office(name: str, state: str, dates: str, kind: str = "Most Recent") -> dict:
    """One creator entry as the Catalog gives it for a land-office series."""
    return {
        "creatorType": kind,
        "heading": f"Department of the Interior. General Land Office. "
        f"{name} ({state}) Land Office. {dates}",
        "authorityType": "organization",
    }


@pytest.fixture
def land_office_series() -> list[dict]:
    """Two series hits with the same title, told apart only by their creator.

    Shapes and headings captured from the live Catalog on 2026-10-05
    (NAIDs 7820442 and 7820365, both "Homestead Final Certificates" in RG
    49). The second lists three predecessor offices before the most recent
    one, which is the order the Catalog really gives.
    """
    rg49 = {
        "levelOfDescription": "recordGroup",
        "title": "Records of the Bureau of Land Management",
        "naId": 378,
        "recordGroupNumber": 49,
    }
    return [
        {
            "naId": 7820442,
            "title": "Homestead Final Certificates",
            "levelOfDescription": "series",
            "creators": [_land_office("Sidney", "Nebraska", "7/2/1887-2/28/1906")],
            "ancestors": [rg49],
        },
        {
            "naId": 7820365,
            "title": "Homestead Final Certificates",
            "levelOfDescription": "series",
            "creators": [
                _land_office("Omaha", "Nebraska", "12/28/1855-ca. 5/21/1869", "Predecessor"),
                _land_office("West Point", "Nebraska", "6/1/1869-ca. 8/31/1873", "Predecessor"),
                _land_office("Norfolk", "Nebraska", "9/1/1873-9/10/1881", "Predecessor"),
                _land_office("Neligh", "Nebraska", "9/1/1881-ca. 2/15/1894"),
            ],
            "ancestors": [rg49],
        },
    ]


@pytest.fixture
def land_entry_file() -> dict:
    """A digitised file unit whose series names its land office only as creator.

    Shape captured from NAID 63668992 on 2026-10-05: the file unit carries
    no creators of its own, and its series ancestor carries two. The
    entrant's name in the title is replaced with an invented one.
    """
    return {
        "naId": 63668992,
        "title": "Lincoln Land Office (Nebraska), Homestead Final Certificate "
        "No. 12018 - A. Settler, November 10, 1905",
        "levelOfDescription": "fileUnit",
        "ancestors": [
            {
                "distance": 2,
                "levelOfDescription": "recordGroup",
                "title": "Records of the Bureau of Land Management",
                "naId": 378,
            },
            {
                "distance": 1,
                "levelOfDescription": "series",
                "title": "Homestead Final Certificates",
                "naId": 7820310,
                "creators": [
                    _land_office("Nebraska City", "Nebraska", "9/14/1857-8/27/1868", "Predecessor"),
                    _land_office("Lincoln", "Nebraska", "9/3/1868-4/30/1925"),
                ],
            },
        ],
    }


@pytest.fixture
def transcription_items() -> list[dict]:
    """Two citizen transcriptions of the same file, with an edit history."""
    return [
        {
            "contributionId": "72d186d6-8d88-11ec-b909-0242ac120002",
            "contributionType": "transcription",
            "contribution": "State of Maine, County of York. On this "
            "fourteenth day of July 1837 personally appeared Sarah Hall, "
            "aged 78 years, widow of Joshua Hall...",
            "contributors": [
                {"userName": "first_hand", "naraStaff": False},
                {"userName": "careful_reader", "naraStaff": False},
            ],
            "createdAt": "2018-11-13 20:20:39",
            "target": {"naId": 54765873, "objectId": "8811", "pageNum": 1},
        },
        {
            "contributionId": "82fb3958-8d88-11ec-b909-0242ac120002",
            "contributionType": "transcription",
            "contribution": "...married in Berwick on the 2nd of March 1782 by Elder Tozier.",
            "contributor": {"userName": "archivist_jo", "naraStaff": True},
            "createdAt": "2019-02-01 09:00:00",
            "target": {"naId": 54765873, "objectId": "8812", "pageNum": 2},
        },
    ]


@pytest.fixture
def tag_items() -> list[dict]:
    """Citizen tags, which on a pension file are usually people's names."""
    return [
        {
            "contributionId": "aaa-1",
            "contributionType": "tag",
            "contribution": "Sarah Hall",
            "contributor": {"userName": "tagger", "naraStaff": False},
            "createdAt": "2020-01-29 08:11:00",
            "target": {"naId": 54765873},
        },
        {
            "contributionId": "aaa-2",
            "contributionType": "tag",
            "contribution": "Berwick, Maine",
            "contributor": {"userName": "tagger", "naraStaff": False},
            "createdAt": "2020-01-29 08:12:00",
            "target": {"naId": 54765873},
        },
    ]


@pytest.fixture
def comment_items() -> list[dict]:
    """A researcher's note on the file."""
    return [
        {
            "contributionId": "ccc-1",
            "contributionType": "comment",
            "contribution": "The bounty land warrant for this soldier is "
            "filed separately under W.17050 1/2.",
            "contributor": {"fullName": "A. Researcher", "naraStaff": False},
            "createdAt": "2021-06-04 12:00:00",
            "target": {"naId": 54765873},
        }
    ]


@pytest.fixture
def extracted_text_payload() -> dict:
    """What /extractedText actually returns.

    A paginated object carrying ``digitalObjects``, each nesting its text at
    ``otherExtractedText.contribution`` alongside the contributor -- often a
    partner such as FamilySearch rather than NARA. Shape captured from the
    live Catalog on 2026-09-23; the spec describes it only in prose. The
    contributor's name is invented.
    """
    return {
        "naId": "54765873",
        "page": 1,
        "limit": 20,
        "total": 2,
        "digitalObjects": [
            {
                "objectId": "8811",
                "otherExtractedText": {
                    "contribution": "PENSION OFFICE " + "x" * 5000,
                    "contributor": {"partnerFullName": "FamilySearch"},
                },
            },
            {
                "objectId": "8812",
                "otherExtractedText": {
                    "contribution": "Declaration of Sarah Hall",
                    "contributor": {"fullName": "A. Archivist"},
                },
            },
        ],
    }


@pytest.fixture
def messy_record() -> dict:
    """A record with nulls where lists belong, and junk inside the lists.

    Nothing here is invented: the Catalog returns nulls for absent repeating
    fields and occasionally a null member inside a present one.
    """
    return {
        "naId": 9999999,
        "title": "   ",
        "levelOfDescription": None,
        "ancestors": None,
        "physicalOccurrences": [None, {"referenceUnits": None}, {}],
        "digitalObjects": [None, {"objectUrl": None}, {"objectUrl": "https://x/1.jpg"}],
        "onlineResources": [None, {"url": None}],
        "scopeAndContentNote": None,
    }


def cache_entries(cache_dir: Path) -> list[Path]:
    """The cached responses in a cache directory, leaving the call ledger out."""
    return sorted(p for p in cache_dir.glob("*.json") if p.name != BUDGET_FILE)


@pytest.fixture
def client(tmp_path) -> NaraClient:
    """A client whose cache lives in a temp directory."""
    return NaraClient("test-key", tmp_path / "cache", timeout=5.0)


@pytest.fixture
def served(tmp_path, monkeypatch) -> NaraClient:
    """Point the server's lazy state at a throwaway client and config.

    Without this a tool call would try to load a real API key from the
    environment, and would write to the developer's own cache directory.
    """
    cache = tmp_path / "server-cache"
    client = NaraClient("k", cache, timeout=5.0)
    monkeypatch.setattr(server.state, "client", client)
    monkeypatch.setattr(server.state, "config", Config(api_key="k", cache_dir=cache, timeout=5.0))
    monkeypatch.setattr(server.state, "aad", aad_client(cache / "aad"))
    return client


async def _no_sleep(seconds: float) -> None:
    """Pacing is tested on its own; elsewhere a test should not wait for it."""


def aad_client(cache_dir: Path, **kwargs) -> AadClient:
    """An AAD client with a throwaway cache that never sleeps."""
    return AadClient(cache_dir, timeout=5.0, sleep=_no_sleep, **kwargs)


#: Pages captured from aad.archives.gov on 2026-10-05, scripts removed. The
#: people in them are historical: Irving Berlin's family (as BEILIN) and other
#: passengers of the 1890s, and Elvis Aron Presley's NUMIDENT row with an
#: entrant born in 1882. Results pages were trimmed to those rows, and the
#: PRESLEY page's count was cut from 9 to 3 to match.
AAD_FIXTURES = Path(__file__).parent / "fixtures" / "aad"


def aad_page(name: str) -> str:
    """One captured AAD page, by file name without ``.html``."""
    return (AAD_FIXTURES / f"{name}.html").read_text(encoding="utf-8")


async def call_tool(tool_name: str, /, **arguments) -> dict:
    """Invoke a tool the way a client does, so Field defaults are resolved.

    Calling a tool function directly in Python hands it ``FieldInfo`` objects
    rather than the declared defaults, which is not how the server is ever
    exercised in practice. The tool name is positional-only so a tool
    parameter called ``name`` cannot collide with it.
    """
    import json

    result = await server.mcp.call_tool(tool_name, arguments)
    return json.loads(result.content[0].text)


# --------------------------------------------------------------------------- #
# Argument building for whole-surface sweeps
# --------------------------------------------------------------------------- #
#: Errors a tool raises from its own input validation, before it does any
#: work. A sweep that receives one of these has not tested what it thinks it
#: has, so :func:`assert_reached_body` treats them as a failure of the sweep
#: rather than of the tool.
LOCAL_VALIDATION_ERRORS = frozenset(
    {
        "no_criteria",
        "refinements_only",
        "invalid_naid",
        "invalid_object_id",
        "invalid_date",
        "conflicting_dates",
        "invalid_level",
        "invalid_congress",
        "no_such_directory",
        "destination_exists",
        "no_such_page",
        "no_such_object",
        "conflicting_page",
        "not_digitised",
        "invalid_file_id",
        "invalid_record_id",
        "invalid_series_id",
        "not_in_category",
        "unknown_field",
        "coded_field",
        "invalid_number",
        "value_too_long",
    }
)

#: Escape hatch for a tool whose "pass at least one of these" rule cannot be
#: satisfied from the schema and the parameter names alone.
#:
#: **Currently empty, and that is the point**: :func:`valid_args` reaches the
#: body of every tool unaided. Add an entry only when it stops doing so, and
#: :func:`assert_reached_body` will tell you which tool needs it.
ARGUMENT_HINTS: dict[str, dict] = {}


def _value_for(name: str, spec: dict):
    """Invent a value a tool will accept for one schema property.

    Driven by the property's schema first and its *name* second. Names carry
    meaning the schema does not: ``naid`` wants digits, ``destination`` wants
    a path, and a text-search parameter wants something to search for. A
    generic string satisfies none of them, and the tool refuses it before
    doing any work.
    """
    if spec.get("enum"):
        return spec["enum"][0]

    lowered = name.lower()
    if "naid" in lowered:
        return "54765873"
    if "object_id" in lowered:
        return "8811"
    if lowered == "file_id":
        return "3259"
    if lowered == "record_id":
        return "470718"
    if lowered.endswith(("_path", "path", "destination")):
        # A new file in a real, writable directory. A tool that writes a
        # file checks its parent exists and refuses to overwrite, so a
        # made-up directory or a reused name would stop the sweep there.
        return str(Path(tempfile.gettempdir()) / f"nara-sweep-{uuid.uuid4().hex}.jpg")
    if "url" in lowered:
        return "https://catalog.archives.gov/sample.jpg"
    if lowered.endswith("_date") or lowered == "exact_date":
        return "1900-01-01" if lowered == "exact_date" else "1900"
    # Any free-text search parameter needs a term, or the tool refuses.
    if lowered.endswith("_text") or lowered in {"query", "title", "creators"}:
        return "Hall"

    # A declared default is the tool's own idea of a sensible value, so it
    # beats any generic this builder would invent. An empty-string default is
    # the exception: a search term of "" is exactly what makes a tool answer
    # no_criteria, so fall through to the generic instead.
    default = spec.get("default")
    if default not in (None, ""):
        return default

    kind = spec.get("type")
    if kind is None:
        # An optional typed parameter (``int | None``) is published as anyOf;
        # the first non-null member is the type that matters.
        kind = next(
            (s.get("type") for s in spec.get("anyOf") or [] if s.get("type") != "null"),
            None,
        )
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "Hall"


def _is_search_term(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"title", "query"} or lowered.endswith("_text")


def valid_args(tool, **overrides) -> dict:
    """Build arguments that carry a tool past its own input validation.

    Covers every required property from the schema, adds a search term for a
    search tool that would otherwise be called without one, then
    applies any :data:`ARGUMENT_HINTS` entry and the caller's overrides.

    Parameters
    ----------
    tool
        A registered tool, as ``mcp.list_tools`` returns it.
    **overrides
        Values to force, beyond what is derived.

    Returns
    -------
    dict
        Arguments to invoke the tool with.
    """
    schema = tool.input_schema or {}
    props = schema.get("properties") or {}
    args = {name: _value_for(name, props.get(name) or {}) for name in schema.get("required") or []}
    # Search tools refuse an empty call, whether everything is optional or
    # only the thing to search in is required (an AAD file), so supply the
    # first parameter that looks like a search term when none is given yet.
    if not any(_is_search_term(name) for name in args):
        term = next((name for name in props if _is_search_term(name)), None)
        if term is not None:
            args[term] = _value_for(term, props[term])
    args.update(ARGUMENT_HINTS.get(tool.name, {}))
    args.update(overrides)
    return args


def assert_reached_body(tool_name: str, result) -> None:
    """Fail if a sweep stopped at input validation instead of the tool's body.

    Without this a sweep quietly stops proving anything the moment a tool
    grows a new argument check: the tool returns a tidy error envelope, the
    assertion that "an envelope came back" passes, and the behaviour under
    test is never exercised.
    """
    if isinstance(result, dict) and result.get("error") in LOCAL_VALIDATION_ERRORS:
        raise AssertionError(
            f"{tool_name} rejected the sweep's arguments with "
            f"{result['error']!r} before doing any work, so this sweep did "
            f"not test it. Add an ARGUMENT_HINTS entry for {tool_name} in "
            f"tests/conftest.py. Message was: {result.get('message')!r}"
        )
