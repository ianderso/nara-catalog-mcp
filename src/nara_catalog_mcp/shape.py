"""Shaping Catalog records into compact dicts for tool output.

A raw Catalog record is large and deeply nested. These helpers keep the fields
a researcher needs to decide whether a record is worth opening: what it is,
where it sits in the hierarchy, which reference unit holds it, and whether
page images exist.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any, limit: int | None = None) -> str | None:
    """Coerce a value to trimmed text, optionally truncated."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def digital_objects(record: dict) -> list[dict]:
    """List a record's page images with their object ids, in page order.

    Parameters
    ----------
    record : dict
        A Catalog record.

    Returns
    -------
    list of dict
        One ``{"page", "object_id", "url"}`` per digital object that carries
        a URL, numbered from 1 in the order the Catalog lists them, which is
        page order for a digitised file. The object id is what OCR,
        transcription and tag entries point at; the page number is what
        ``download_page_image`` takes. An object without a URL is skipped
        and does not consume a page number.
    """
    out: list[dict] = []
    for obj in record.get("digitalObjects") or []:
        url = (obj or {}).get("objectUrl")
        if not url:
            continue
        out.append({"page": len(out) + 1, "object_id": _text(obj.get("objectId")), "url": url})
    return out


def digital_object_urls(record: dict) -> list[str]:
    """Extract page-image URLs from a record, in page order."""
    return [image["url"] for image in digital_objects(record)]


def hierarchy(record: dict) -> list[dict]:
    """Summarise a record's ancestors, outermost first.

    Parameters
    ----------
    record : dict
        A Catalog record.

    Returns
    -------
    list of dict
        One ``{"level", "title"}`` per ancestor. The record group and series
        are what make a citation locatable.
    """
    return [
        {
            "level": _text(a.get("levelOfDescription")),
            "title": _text(a.get("title")),
        }
        for a in record.get("ancestors") or []
    ]


def reference_units(record: dict) -> list[str]:
    """List the units that physically hold the record, e.g. a regional archive."""
    return [
        name
        for occurrence in record.get("physicalOccurrences") or []
        for unit in (occurrence or {}).get("referenceUnits") or []
        if (name := _text((unit or {}).get("name")))
    ]


def summarize(record: dict) -> dict:
    """Shape one record for a search-result list.

    Parameters
    ----------
    record : dict
        A Catalog record.

    Returns
    -------
    dict
        Identity, hierarchy, holding units, date coverage and image count.
    """
    images = digital_object_urls(record)
    return {
        "naid": record.get("naId"),
        "title": _text(record.get("title")),
        "level": _text(record.get("levelOfDescription")),
        "record_type": _text(record.get("recordType")),
        "hierarchy": hierarchy(record),
        "reference_units": reference_units(record),
        "coverage_start": _text(record.get("coverageStartDate")),
        "coverage_end": _text(record.get("coverageEndDate")),
        "image_count": len(images),
        "catalog_url": catalog_url(record.get("naId")),
    }


def detail(record: dict) -> dict:
    """Shape one record for a full read, adding the scope note and image URLs.

    Parameters
    ----------
    record : dict
        A Catalog record.

    Returns
    -------
    dict
        Everything :func:`summarize` returns, plus the scope and content note,
        online resources and every page image with its object id.
    """
    out = summarize(record)
    out["scope_and_content"] = _text(record.get("scopeAndContentNote"))
    out["online_resources"] = [
        url for res in record.get("onlineResources") or [] if (url := (res or {}).get("url"))
    ]
    out["images"] = digital_objects(record)
    return out


def catalog_url(naid: Any) -> str | None:
    """Build the public Catalog page URL for a NAID."""
    return f"https://catalog.archives.gov/id/{naid}" if naid else None


def _clipped(value: Any, limit: int) -> tuple[str | None, int, bool]:
    """Trim a long body of text.

    Parameters
    ----------
    value : Any
        The text, or anything coercible to it.
    limit : int
        Maximum characters to keep. Zero or less keeps everything.

    Returns
    -------
    tuple of (str or None, int, bool)
        The kept text, the length of the original, and whether it was cut.
    """
    text = _text(value)
    if text is None:
        return None, 0, False
    if limit > 0 and len(text) > limit:
        return text[:limit], len(text), True
    return text, len(text), False


def contribution(item: dict, *, limit: int = 4000) -> dict:
    """Shape one public contribution: a tag, comment or transcription.

    Parameters
    ----------
    item : dict
        A contribution object from a ``/tags``, ``/comments`` or
        ``/transcriptions`` route.
    limit : int, optional
        Characters of contributed text to keep. A transcribed pension file
        runs to thousands, and a tag to three.

    Returns
    -------
    dict
        The text, who wrote it and when, and which record page it targets.
    """
    target = item.get("target") or {}
    contributor = item.get("contributor") or {}
    if not contributor:
        # A transcription carries an edit history rather than one author.
        history = [c for c in item.get("contributors") or [] if c]
        contributor = history[-1] if history else {}
    text, length, truncated = _clipped(item.get("contribution"), limit)
    out = {
        "contribution_id": _text(item.get("contributionId")),
        "type": _text(item.get("contributionType")),
        "text": text,
        "contributor": _text(contributor.get("userName") or contributor.get("fullName")),
        "nara_staff": contributor.get("naraStaff"),
        "created": _text(item.get("createdAt") or item.get("updatedAt")),
        "naid": target.get("naId"),
        "object_id": target.get("objectId"),
        "page_number": target.get("pageNum"),
        "characters": length,
    }
    if truncated:
        out["truncated"] = True
    return out


def extracted_page(item: dict, *, limit: int = 4000) -> dict:
    """Shape one digital object's OCR text.

    Parameters
    ----------
    item : dict
        One entry from ``digitalObjects`` in an ``/extractedText/{naId}``
        response.
    limit : int, optional
        Characters of text to keep. Zero keeps the lot, which for a long file
        unit is tens of thousands.

    Returns
    -------
    dict
        Object id, the text, its full length, who produced it, and whether it
        was truncated.

    Notes
    -----
    Two routes, two spellings, both verified live. ``/extractedText``
    (2026-09-23) nests partner text at ``otherExtractedText.contribution``
    as one object, with the contributor alongside -- often a partner such as
    FamilySearch rather than NARA. A search hit (2026-09-28) carries NARA's
    own OCR in a flat ``extractedText`` and partner text as a **list** of
    such objects under ``otherExtractedText``. Partner text is preferred
    when both are present, matching what the dedicated route returns; the
    first list entry that carries text is the one read. The contributor is
    carried through because it is the provenance of a reading that the tool
    description calls a lead rather than evidence.
    """
    other = item.get("otherExtractedText")
    if isinstance(other, list):
        other = next(
            (o for o in other if isinstance(o, dict) and _text(o.get("contribution"))),
            {},
        )
    other = other if isinstance(other, dict) else {}
    raw = other.get("contribution") or item.get("extractedText") or item.get("contribution")
    text, length, truncated = _clipped(raw, limit)
    contributor = other.get("contributor")
    contributor = contributor if isinstance(contributor, dict) else {}
    out = {
        "object_id": _text(item.get("objectId")),
        "characters": length,
        "text": text,
        "contributed_by": _text(contributor.get("partnerFullName") or contributor.get("fullName")),
    }
    if truncated:
        out["truncated"] = True
    return out


def record_extracted_text(record: dict, *, limit: int = 2000) -> list[dict]:
    """Shape the OCR text a search hit carries when ``includeExtractedText`` was set.

    Parameters
    ----------
    record : dict
        A Catalog record from a search made with ``includeExtractedText``.
    limit : int, optional
        Characters of text to keep per digital object. A search page holds
        up to a hundred hits, so this is deliberately tighter than
        :func:`extracted_page`'s default.

    Returns
    -------
    list of dict
        One :func:`extracted_page` entry per digital object that actually
        carries text, in page order. Objects without text are left out
        rather than reported as empty, so the list is short for a record
        with one OCR'd page and empty for one with none.

    Notes
    -----
    A search hit carries NARA's OCR in a flat ``extractedText`` on each
    digital object and partner text as a list under ``otherExtractedText``;
    :func:`extracted_page` reads both. Verified live 2026-09-28; see
    ``docs/API-NOTES.md``.
    """
    out = []
    for obj in record.get("digitalObjects") or []:
        if not isinstance(obj, dict):
            continue
        page = extracted_page(obj, limit=limit)
        if page["characters"]:
            out.append(page)
    return out


def availability_entry(item: dict) -> dict:
    """Shape one online-availability entry: where else a record lives online.

    Parameters
    ----------
    item : dict
        An entry from ``/online-availability/naId/{naId}``.

    Returns
    -------
    dict
        Identity, the availability status NARA records, and any URLs given.
    """
    urls = [
        url for res in item.get("onlineResources") or [] if (url := _text((res or {}).get("url")))
    ]
    return {
        "naid": item.get("naId"),
        "title": _text(item.get("title")),
        "status": _text(item.get("status")),
        "availability": _text(item.get("availability")),
        "urls": urls or digital_object_urls(item),
        "updated": _text(item.get("updatedAt") or item.get("createdAt")),
    }
