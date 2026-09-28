"""Tests for the sweep's own argument builder and drift guard.

A whole-surface sweep is only as good as the arguments it invokes tools with.
When those stop being valid, the tool returns a tidy error envelope, the
sweep's "an envelope came back" assertion passes, and the behaviour under
test quietly stops being tested. That happened here once already, so the
machinery is tested rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import (
    LOCAL_VALIDATION_ERRORS,
    assert_reached_body,
    valid_args,
)


class _Tool:
    """A stand-in for a registered tool, carrying only what the builder reads."""

    def __init__(self, name: str, schema: dict):
        self.name = name
        self.input_schema = schema


def _schema(required: list[str], **props) -> dict:
    return {"type": "object", "properties": props, "required": required}


# --------------------------------------------------------------------------- #
# The drift guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", sorted(LOCAL_VALIDATION_ERRORS))
async def test_the_guard_fires_on_every_local_validation_error(code):
    """Each of these means the sweep never reached the tool's body."""
    with pytest.raises(AssertionError) as exc:
        assert_reached_body("some_tool", {"error": code, "message": "nope"})
    assert "did not test it" in str(exc.value)
    assert "some_tool" in str(exc.value)


async def test_the_guard_names_the_fix():
    """An assertion nobody can act on is a worse failure than none."""
    with pytest.raises(AssertionError) as exc:
        assert_reached_body("search_records", {"error": "no_criteria"})
    assert "ARGUMENT_HINTS" in str(exc.value)


async def test_the_guard_allows_a_genuine_api_failure_through():
    """An injected API error is the thing a sweep is trying to observe."""
    assert_reached_body("get_record", {"error": "api_error", "status": 500})


async def test_the_guard_allows_a_successful_result():
    """Not every sweep injects a failure."""
    assert_reached_body("api_budget", {"live_calls_this_session": 0})


# --------------------------------------------------------------------------- #
# The builder
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "check"),
    [
        ("naid", lambda v: str(v).isdigit()),
        ("parent_naid", lambda v: str(v).isdigit()),
        ("object_id", lambda v: str(v).isdigit()),
        ("image_url", lambda v: v.startswith("https://")),
        ("transcription_text", lambda v: bool(v)),
        ("tag_text", lambda v: bool(v)),
    ],
)
async def test_parameter_names_drive_plausible_values(name, check):
    """Names carry meaning the schema does not.

    A parameter called naid wants digits and a *_text parameter wants
    something to search for; a generic string is refused before the tool
    does any work.
    """
    tool = _Tool("t", _schema([name], **{name: {"type": "string"}}))
    assert check(valid_args(tool)[name])


async def test_a_destination_is_a_real_writable_directory():
    """A tool that writes a file checks its parent first.

    A made-up path stops the sweep at that check -- which is exactly how the
    guard caught download_page_image.
    """
    tool = _Tool("t", _schema(["destination"], destination={"type": "string"}))
    assert Path(valid_args(tool)["destination"]).parent.is_dir()


async def test_a_destination_is_a_file_that_does_not_exist_yet():
    """A tool that never overwrites refuses a reused name.

    A fixed sample path would make the sweep depend on whatever an earlier
    run, or anything else on the machine, left in the temp directory.
    """
    tool = _Tool("t", _schema(["destination"], destination={"type": "string"}))
    first, second = (valid_args(tool)["destination"] for _ in range(2))
    assert first != second
    assert not Path(first).exists()


async def test_an_enum_takes_its_first_member():
    """A generic string would be rejected by a constrained parameter."""
    tool = _Tool("t", _schema(["level"], level={"enum": ["series", "item"]}))
    assert valid_args(tool)["level"] == "series"


async def test_a_search_tool_with_no_required_parameters_still_gets_a_term():
    """Search tools declare everything optional but refuse an empty call."""
    tool = _Tool("search_records", _schema([], title={"type": "string"}, limit={"type": "integer"}))
    args = valid_args(tool)
    assert args.get("title") == "Hall"


async def test_only_one_search_term_is_supplied():
    """Filling every field would test a different, narrower call."""
    tool = _Tool(
        "search_records_advanced", _schema([], title={"type": "string"}, query={"type": "string"})
    )
    assert len(valid_args(tool)) == 1


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("integer", 1), ("number", 1.0), ("boolean", False), ("array", [])],
)
async def test_types_without_a_name_hint_fall_back_to_the_schema(kind, expected):
    """The schema is the second source of truth after the name."""
    tool = _Tool("t", _schema(["v"], v={"type": kind}))
    assert valid_args(tool)["v"] == expected


async def test_an_optional_typed_parameter_takes_its_real_type():
    """`int | None` is published as anyOf; the null member is not the type."""
    tool = _Tool(
        "t", _schema(["n"], n={"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None})
    )
    assert valid_args(tool)["n"] == 1


async def test_overrides_win_over_everything_derived():
    """A caller testing a specific case must be able to force a value."""
    tool = _Tool("t", _schema(["naid"], naid={"type": "string"}))
    assert valid_args(tool, naid="999")["naid"] == "999"


async def test_a_tool_with_nothing_to_supply_gets_nothing():
    """api_budget takes no arguments; that is not a gap to fill."""
    assert valid_args(_Tool("api_budget", _schema([]))) == {}


async def test_an_empty_string_default_is_not_used_as_a_value():
    """A search term of "" is exactly what makes a tool answer no_criteria."""
    tool = _Tool("t", _schema(["title"], title={"type": "string", "default": ""}))
    assert valid_args(tool)["title"] == "Hall"


async def test_a_meaningful_default_beats_the_generic_fallback():
    """A declared default is the tool's own idea of a sensible value."""
    tool = _Tool("t", _schema(["limit"], limit={"type": "integer", "default": 20}))
    assert valid_args(tool)["limit"] == 20
