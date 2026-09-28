"""Record shaping: the fields a researcher needs, and nothing invented."""

from __future__ import annotations

from nara_catalog_mcp.shape import (
    availability_entry,
    catalog_url,
    contribution,
    detail,
    digital_object_urls,
    digital_objects,
    extracted_page,
    hierarchy,
    record_extracted_text,
    reference_units,
    summarize,
)


def test_summarize_core_fields(record):
    """A summary carries identity, provenance and whether images exist."""
    out = summarize(record)
    assert out["naid"] == 54765873
    assert out["image_count"] == 2
    assert out["reference_units"] == ["National Archives Building"]
    assert out["catalog_url"] == "https://catalog.archives.gov/id/54765873"


def test_summarize_omits_the_scope_note(record):
    """The scope note belongs to the full read, keeping result lists compact."""
    assert "scope_and_content" not in summarize(record)


def test_detail_adds_scope_and_images(record):
    """A full read carries the note and every page URL in order."""
    out = detail(record)
    assert out["scope_and_content"] == "Widow's pension application."
    assert out["images"] == [
        {"page": 1, "object_id": "8811", "url": "https://catalog.archives.gov/OpaAPI/media/1.jpg"},
        {"page": 2, "object_id": "8812", "url": "https://catalog.archives.gov/OpaAPI/media/2.jpg"},
    ]


def test_an_object_without_a_url_does_not_consume_a_page_number():
    """Page numbers count the pages a caller can fetch, so download and list agree."""
    record = {
        "digitalObjects": [
            {"objectId": "1", "objectUrl": "https://x/1.jpg"},
            {"objectId": "2"},
            {"objectId": "3", "objectUrl": "https://x/3.jpg"},
        ]
    }
    assert [(i["page"], i["object_id"]) for i in digital_objects(record)] == [(1, "1"), (2, "3")]
    assert digital_object_urls(record) == ["https://x/1.jpg", "https://x/3.jpg"]


def test_hierarchy_is_outermost_first(record):
    """Record group before series, which is how a citation is written."""
    levels = [h["level"] for h in hierarchy(record)]
    assert levels == ["recordGroup", "series"]


def test_undigitised_record_has_no_images():
    """A description with no digital objects reports zero, not an error."""
    assert digital_object_urls({"naId": 1, "title": "Paper only"}) == []
    assert summarize({"naId": 1})["image_count"] == 0


def test_missing_nested_structures_are_tolerated():
    """Absent or null nesting must not raise; Catalog records are uneven."""
    sparse = {"naId": 7, "ancestors": None, "physicalOccurrences": None}
    assert hierarchy(sparse) == []
    assert reference_units(sparse) == []
    assert summarize(sparse)["title"] is None


def test_catalog_url_needs_a_naid():
    """No NAID means no URL rather than a broken one."""
    assert catalog_url(None) is None


def test_undigitised_description_still_summarises(undigitised_record):
    """A paper-only record must say who holds it; that is the whole answer."""
    out = summarize(undigitised_record)
    assert out["image_count"] == 0
    assert out["reference_units"] == ["National Archives at St. Louis"]
    assert out["level"] == "fileUnit"


def test_messy_nesting_does_not_raise(messy_record):
    """Nulls inside present lists are common and must not break shaping."""
    out = detail(messy_record)
    assert out["hierarchy"] == []
    assert out["reference_units"] == []
    assert out["online_resources"] == []
    assert out["images"] == [{"page": 1, "object_id": None, "url": "https://x/1.jpg"}]


def test_whitespace_title_is_absent_not_blank(messy_record):
    """A title of spaces is no title; report None rather than fake content."""
    assert summarize(messy_record)["title"] is None


def test_contribution_carries_text_author_and_page(tag_items):
    """A tag is only a lead if you can see who made it and on what."""
    out = contribution(tag_items[0])
    assert out["text"] == "Sarah Hall"
    assert out["contributor"] == "tagger"
    assert out["type"] == "tag"
    assert out["naid"] == 54765873


def test_transcription_author_comes_from_the_edit_history(transcription_items):
    """A transcription has contributors, not a contributor; use the latest."""
    out = contribution(transcription_items[0])
    assert out["contributor"] == "careful_reader"
    assert out["page_number"] == 1
    assert out["object_id"] == "8811"


def test_long_contribution_is_truncated_and_says_so(transcription_items):
    """Silent truncation would let a caller quote half a sentence as whole."""
    out = contribution(transcription_items[0], limit=20)
    assert len(out["text"]) == 20
    assert out["truncated"] is True
    assert out["characters"] > 20


def test_short_contribution_is_not_flagged_as_truncated(tag_items):
    """The truncated flag only appears when something was actually cut."""
    assert "truncated" not in contribution(tag_items[0])


def test_extracted_page_reports_full_length_when_clipped(extracted_text_payload):
    """The caller needs to know how much OCR text it has not seen."""
    out = extracted_page(extracted_text_payload["digitalObjects"][0], limit=100)
    assert out["object_id"] == "8811"
    assert len(out["text"]) == 100
    assert out["characters"] > 5000
    assert out["truncated"] is True


def test_extracted_page_limit_zero_keeps_everything(extracted_text_payload):
    """A caller that asks for the whole page gets the whole page."""
    out = extracted_page(extracted_text_payload["digitalObjects"][0], limit=0)
    assert len(out["text"]) == out["characters"]
    assert "truncated" not in out


def test_extracted_page_without_text_is_empty_not_missing():
    """An object with no OCR reports zero characters rather than raising."""
    out = extracted_page({"objectId": "1"})
    assert out == {
        "object_id": "1",
        "characters": 0,
        "text": None,
        "contributed_by": None,
    }


def test_record_extracted_text_keeps_only_pages_that_carry_text(record):
    """One OCR'd page out of two yields one entry, not a stub for the other."""
    record["digitalObjects"][1]["extractedText"] = "page two"
    out = record_extracted_text(record)
    assert [(p["object_id"], p["text"]) for p in out] == [("8812", "page two")]


def test_record_extracted_text_clips_each_page(record):
    """The cap is per page, and the entry says what was cut."""
    record["digitalObjects"][0]["extractedText"] = "y" * 50
    (entry,) = record_extracted_text(record, limit=10)
    assert len(entry["text"]) == 10
    assert entry["characters"] == 50
    assert entry["truncated"] is True


def test_record_extracted_text_tolerates_messy_objects(messy_record):
    """Nulls inside digitalObjects are real, and must not raise."""
    assert record_extracted_text(messy_record) == []


def test_availability_entry_prefers_online_resource_urls():
    """Where else the record lives is the point of the entry."""
    out = availability_entry(
        {
            "naId": 1,
            "status": "active",
            "availability": "fullyDigitized",
            "onlineResources": [{"url": "https://partner.example/img"}, None],
        }
    )
    assert out["urls"] == ["https://partner.example/img"]
    assert out["availability"] == "fullyDigitized"


def test_availability_entry_falls_back_to_digital_objects():
    """An entry with no online resources still offers its own page images."""
    entry = {"naId": 1, "digitalObjects": [{"objectUrl": "https://x/1.jpg"}]}
    assert availability_entry(entry)["urls"] == ["https://x/1.jpg"]


# --------------------------------------------------------------------------- #
# Shapes captured from the live Catalog on 2026-09-23. The spec describes these
# responses only in prose, and each differed from the obvious reading. Names of
# living people have been replaced; the structure is as captured.
# --------------------------------------------------------------------------- #
LIVE_EXTRACTED_TEXT = {
    "naId": "54765873",
    "page": 1,
    "limit": 20,
    "total": 58,
    "digitalObjects": [
        {
            "objectId": "54765874",
            "otherExtractedText": {
                "contributionId": "4d2c7095-4a74-482f-8e1f-d99125edb8f0",
                "createdAt": "2024-11-19 22:31:35",
                "contribution": "SER\nConn\nCONTENTS\nHall\nJoshua\nRhoda\nW. 17050",
                "contributor": {
                    "naraStaff": True,
                    "partnerFullName": "FamilySearch",
                    "fullName": "A. Archivist",
                },
            },
        }
    ],
}

LIVE_NO_AVAILABILITY = {"response": "No Online Availability status available for naId 54765873."}


def test_extracted_text_is_read_from_where_the_api_puts_it():
    """The text is nested under otherExtractedText.contribution.

    Reading a flat `extractedText` key returns empty text for every page,
    which looks like a record with no OCR rather than a parsing bug.
    """
    from nara_catalog_mcp.client import unwrap_any

    items = unwrap_any(LIVE_EXTRACTED_TEXT)
    assert len(items) == 1
    page = extracted_page(items[0])
    assert page["text"].startswith("SER")
    assert page["characters"] > 0


def test_extracted_text_carries_its_contributor():
    """Provenance matters for a reading the tool calls a lead, not evidence.

    Much of NARA's extracted text was contributed by partners such as
    FamilySearch, not produced by NARA.
    """
    from nara_catalog_mcp.client import unwrap_any

    page = extracted_page(unwrap_any(LIVE_EXTRACTED_TEXT)[0])
    assert page["contributed_by"] == "FamilySearch"


def test_extracted_text_falls_back_to_a_flat_key():
    """Not every entry is a partner contribution; do not require the nesting."""
    page = extracted_page({"objectId": "1", "extractedText": "plain text"})
    assert page["text"] == "plain text"
    assert page["contributed_by"] is None


#: One digital object as a search hit carries it with both text flags set.
#: The two paths were captured live on 2026-09-28; the fields inside a list
#: entry follow the object the /extractedText route returns.
LIVE_SEARCH_HIT_OBJECT = {
    "objectId": "54765874",
    "objectUrl": "https://catalog.archives.gov/OpaAPI/media/54765874.jpg",
    "extractedText": "SER Conn CONTENTS Hall Joshua Rhoda W. 17050",
    "otherExtractedText": [
        {
            "contribution": "SER\nConn\nCONTENTS\nHall\nJoshua\nRhoda\nW. 17050",
            "contributor": {"partnerFullName": "FamilySearch"},
        }
    ],
}


def test_a_search_hit_lists_partner_text_and_it_is_read():
    """The search route delivers otherExtractedText as a list, not an object.

    Before this was handled, the list was skipped and the fold fell back to
    NARA's OCR -- or to nothing, on a record with partner text alone.
    """
    page = extracted_page(LIVE_SEARCH_HIT_OBJECT)
    assert page["text"].startswith("SER\nConn")
    assert page["contributed_by"] == "FamilySearch"


def test_partner_text_alone_in_a_list_is_not_lost():
    """The case that was silently empty: no NARA OCR, only a partner's list."""
    obj = {"objectId": "1", "otherExtractedText": [{"contribution": "widow Sarah"}]}
    assert extracted_page(obj)["text"] == "widow Sarah"


def test_the_first_list_entry_with_text_wins():
    """Empty and junk entries are skipped; the first real reading is taken."""
    obj = {
        "objectId": "1",
        "otherExtractedText": [
            None,
            {},
            {"contribution": "  "},
            {"contribution": "b"},
            {"contribution": "c"},
        ],
    }
    assert extracted_page(obj)["text"] == "b"


def test_a_list_with_no_text_falls_back_to_naras_ocr():
    """An empty or textless list must not hide the flat extractedText."""
    for other in ([], [None], [{"contribution": None}]):
        obj = {"objectId": "1", "extractedText": "nara", "otherExtractedText": other}
        assert extracted_page(obj)["text"] == "nara"


def test_unwrap_finds_the_digital_objects_container():
    """/extractedText paginates an object; it does not return a bare array."""
    from nara_catalog_mcp.client import unwrap_any

    assert len(unwrap_any(LIVE_EXTRACTED_TEXT)) == 1
    assert unwrap_any(LIVE_EXTRACTED_TEXT)[0]["objectId"] == "54765874"
