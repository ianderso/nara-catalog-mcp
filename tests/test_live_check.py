"""The live-check script's pure parts.

The script itself needs a key and the network; its helpers do not, and a
helper that mis-reads the payload would send the user chasing the wrong path.
"""

from __future__ import annotations

from tests import live_check


def test_long_strings_reports_paths_and_lengths():
    """Nested text is found with the path a reader can follow to it."""
    payload = {
        "body": {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "record": {
                                "title": "short",
                                "digitalObjects": [{"objectId": "1", "extractedText": "x" * 200}],
                            }
                        }
                    }
                ]
            }
        }
    }
    assert live_check.long_strings(payload) == [
        ("$.body.hits.hits[0]._source.record.digitalObjects[0].extractedText", 200)
    ]


def test_long_strings_ignores_identifiers_and_junk():
    """Short strings, numbers and nulls are not text."""
    assert live_check.long_strings({"naId": "54765873", "n": 3, "x": None}) == []


def test_the_sent_set_carries_the_names_the_server_really_sends():
    """Read from the real source, so the audit cannot drift from the code."""
    sent = live_check.parameters_the_server_sends()
    assert {
        "startDate",
        "includeExtractedText",
        "includeOtherExtractedText",
        "transcriptionContribution",
        "searchAfter",
        "naId",
        "sourceIncludes",
        "microformPublicationsIdentifier",
        "microformPublicationsIdentifier_is",
        "title_is",
        "controlNumbers",
        "congressNumber",
        "contributions_exist",
    } <= sent


def test_the_sent_set_is_meant_to_be_intersected_with_the_spec():
    """It over-matches output keys by design; the spec is what disambiguates."""
    src = """
        out = {
            "total": total,
            "records": summaries,
        }
        params: dict = {
            "startDate": start_date or None,
        }
    """
    sent = live_check.parameters_the_server_sends(src)
    assert "startDate" in sent
    assert "total" in sent  # over-match, filtered by the caller
    assert sent & {"startDate", "endDate"} == {"startDate"}


SWAGGER_2 = {
    "parameters": {
        "paramQuery": {"name": "q", "in": "query", "type": "string"},
        "paramSearchAfterRecord": {"name": "searchAfter", "in": "query", "type": "integer"},
    },
    "paths": {
        "/records/search": {
            "get": {
                "parameters": [
                    {"$ref": "#/parameters/paramQuery"},
                    {"$ref": "#/parameters/paramSearchAfterRecord"},
                    {"name": "limit", "in": "query", "type": "integer"},
                ]
            }
        }
    },
}

OPENAPI_3 = {
    "components": {"parameters": {"q": {"name": "q", "in": "query"}}},
    "paths": {
        "/records/search": {
            "parameters": [{"$ref": "#/components/parameters/q"}],
            "get": {"parameters": [{"name": "title", "in": "query"}]},
        }
    },
}


def test_search_parameters_follow_swagger_2_refs():
    """Shared parameters live at the top level and are referenced by name."""
    params = live_check.search_parameters(SWAGGER_2)
    assert sorted(params) == ["limit", "q", "searchAfter"]
    assert params["searchAfter"]["type"] == "integer"


def test_search_parameters_read_path_level_and_openapi_3_refs():
    """Parameters may sit on the path item, and refs may point into components."""
    assert sorted(live_check.search_parameters(OPENAPI_3)) == ["q", "title"]


def test_an_unresolvable_ref_drops_one_parameter_not_the_audit():
    """A dangling pointer is the spec's bug; the rest must still resolve."""
    spec = {
        "paths": {
            "/records/search": {
                "get": {"parameters": [{"$ref": "#/parameters/gone"}, {"name": "q"}]}
            }
        }
    }
    assert sorted(live_check.search_parameters(spec)) == ["q"]
    assert live_check.resolve_ref(spec, "http://elsewhere#/x") == {}


def test_a_spec_without_the_path_resolves_to_nothing():
    """Nothing, rather than a KeyError, so the diagnostic can run."""
    assert live_check.search_parameters({}) == {}
    assert live_check.search_parameters({"paths": {"/records/search": None}}) == {}


def test_probe_values_follow_the_schema():
    """An enum takes its first member; a bare string gets a placeholder."""
    assert live_check.probe_value({"enum": ["item", "series"]}) == "item"
    assert live_check.probe_value({"type": "boolean"}) is True
    assert live_check.probe_value({"schema": {"type": "integer"}}) == 1
    assert live_check.probe_value({"type": "string"}) == "test"


def test_distinct_creators_counts_names_and_differences():
    """Two same-titled series with two offices, and one naming none."""
    summaries = [{"creator": "Sidney"}, {"creator": "Neligh"}, {"creator": "Sidney"}, {}]
    assert live_check.distinct_creators(summaries) == (3, 2)
    assert live_check.distinct_creators([]) == (0, 0)
