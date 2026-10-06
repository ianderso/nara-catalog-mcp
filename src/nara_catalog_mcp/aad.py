"""Reading NARA's Access to Archival Databases (AAD), ``aad.archives.gov``.

AAD serves a selection of NARA's electronic records -- the NUMIDENT, the
WWII Army enlistment cards, the 1820-1912 ship passenger lists and others --
as searchable tables. The Catalog API does not reach them, and AAD has no
API of its own: it is a JSP web application. This module reads its pages the
way a person's browser does, one at a time, and parses the HTML.

What was observed live on 2026-10-05, and what follows from it (the full
notes are in ``docs/API-NOTES.md``):

* **One host.** A request hook refuses any host but ``aad.archives.gov``, so
  nothing a model passes in can make this client fetch another site.
* **An identifying User-Agent.** AAD's load balancer answers 403 to a bare
  ``nara-catalog-mcp/<version>`` agent and admits the ``Mozilla/5.0
  (compatible; <name>; +<url>)`` form that Googlebot and Bingbot use. The
  client sends that form, naming this project and linking to it.
* **Session cookies and a "Please Wait" page.** A search first answers with a
  page that refreshes itself while AAD works; asking again with the same
  session cookies returns the results.
* **Polite pacing.** One request in flight and at least ``interval`` seconds
  between the starts of two requests. No robots.txt exists on the host, and
  no terms of use forbid automated access.
* **Parsed answers are cached** on disk for ``ttl`` seconds, with the date
  they were fetched, which is the retrieval date a citation needs. A page
  that fails to parse is never cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from . import __version__
from .client import _write_atomically

logger = logging.getLogger("nara_catalog_mcp.aad")

#: The one host this client will talk to.
AAD_HOST = "aad.archives.gov"

#: Base of every AAD page.
AAD_BASE = f"https://{AAD_HOST}/aad/"

#: Where the project lives; named in the User-Agent.
PROJECT_URL = "https://github.com/ianderso/nara-catalog-mcp"

#: Least time between the starts of two requests to AAD, in seconds.
MIN_INTERVAL = 2.0

#: How long a parsed page is kept. AAD reloads a file now and then, and a
#: reload can renumber its records, so nothing is kept for ever.
CACHE_TTL = 7 * 86_400.0

#: How many times a "Please Wait" page is asked again before giving up.
MAX_WAIT_POLLS = 5

#: Rows per results page; 50 is the most AAD offers.
PAGE_SIZE = 50

#: Bumped when a parser changes what it returns, so older cache entries are
#: not served in the old shape.
PARSER_VERSION = 1

#: AAD's browse categories, by the name the tools take.
CATEGORIES = {
    "genealogy": "GP21,22,23,24,44",
    "casualties": "GP21",
    "civilians": "GP22",
    "military": "GP23",
    "prisoners_of_war": "GP24",
    "immigrants": "GP44",
}


def user_agent() -> str:
    """The User-Agent sent to AAD: the crawler convention, naming this project."""
    return f"Mozilla/5.0 (compatible; nara-catalog-mcp/{__version__}; +{PROJECT_URL})"


class AadError(RuntimeError):
    """AAD refused a request or did not answer.

    ``status`` is the HTTP status, or 0 when no response arrived.
    """

    def __init__(self, status: int, detail: str, *, url: str):
        """Record the failing request and what AAD said."""
        self.status = status
        self.detail = detail
        self.url = url
        super().__init__(f"GET {url} -> {status or 'no response'}: {detail}")


class AadBusy(RuntimeError):
    """AAD was still showing its "Please Wait" page after every poll."""


class AadLayoutError(RuntimeError):
    """A page did not have the structure the parser expects."""


class HostNotAllowed(RuntimeError):
    """A request was aimed at a host other than AAD."""


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
class AadClient:
    """Paced, cached client for AAD's pages.

    Parameters
    ----------
    cache_dir : Path
        Directory for parsed answers. Created on first write.
    timeout : float, optional
        Per-request timeout in seconds.
    interval : float, optional
        Least time between the starts of two requests.
    ttl : float, optional
        Seconds a cached answer is served before it is fetched again.
    transport, clock, sleep, now
        Seams for the tests.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        timeout: float = 60.0,
        interval: float = MIN_INTERVAL,
        ttl: float = CACHE_TTL,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._cache_dir = cache_dir
        self._interval = interval
        self._ttl = ttl
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._http = httpx.AsyncClient(
            base_url=AAD_BASE,
            timeout=timeout,
            headers={"User-Agent": user_agent(), "Accept": "text/html"},
            event_hooks={"request": [_only_aad]},
            follow_redirects=False,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._last_start: float | None = None
        self.live_calls = 0
        self.cache_hits = 0

    async def aclose(self) -> None:
        """Close the HTTP transport."""
        await self._http.aclose()

    async def read(
        self,
        page: str,
        params: dict[str, Any],
        parse: Callable[[str], dict],
        *,
        refresh: bool = False,
    ) -> dict:
        """Fetch one AAD page, parse it, and cache what the parser returned.

        Parameters
        ----------
        page : str
            The JSP page below ``/aad/``, e.g. ``"record-detail.jsp"``.
        params : dict
            Query parameters. Lists become repeated parameters, which is how
            AAD takes the two ends of a range.
        parse : callable
            Turns the page's HTML into a dict. If it raises, nothing is cached.
        refresh : bool, optional
            Fetch again even if a fresh answer is cached.

        Returns
        -------
        dict
            The parsed page, with ``retrieved`` (the UTC date it was
            fetched, as YYYY-MM-DD) and ``url`` (the page's address) added.
        """
        url = page_url(page, params)
        key = hashlib.sha256(f"v{PARSER_VERSION} {url}".encode()).hexdigest()[:24]
        path = self._cache_dir / f"{key}.json"
        if not refresh and (cached := self._cached(path)) is not None:
            self.cache_hits += 1
            return cached
        text = await self._fetch(page, params, url)
        parsed = parse(text)
        fetched = self._now()
        parsed["retrieved"] = fetched.strftime("%Y-%m-%d")
        parsed["url"] = url
        _write_atomically(
            path, json.dumps({"fetched": fetched.isoformat(timespec="seconds"), "data": parsed})
        )
        return parsed

    def _cached(self, path: Path) -> dict | None:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(entry["fetched"])
            data = entry["data"]
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        if (self._now() - fetched).total_seconds() > self._ttl:
            return None
        return data

    async def _fetch(self, page: str, params: dict, url: str) -> str:
        """GET a page, asking again while AAD answers "Please Wait"."""
        for _ in range(MAX_WAIT_POLLS + 1):
            text = await self._get_once(page, params, url)
            if not is_please_wait(text):
                return text
        raise AadBusy(
            f"AAD was still searching after {MAX_WAIT_POLLS} polls, "
            f"{self._interval:g} s apart. Try again in a minute."
        )

    async def _get_once(self, page: str, params: dict, url: str) -> str:
        async with self._lock:
            if self._last_start is not None:
                wait = self._interval - (self._clock() - self._last_start)
                if wait > 0:
                    await self._sleep(wait)
            self._last_start = self._clock()
            self.live_calls += 1
            try:
                resp = await self._http.get(page, params=_query(params))
            except httpx.TransportError as exc:
                raise AadError(0, f"{type(exc).__name__}: {exc}".rstrip(": "), url=url) from exc
        if resp.status_code >= 300:
            if resp.is_redirect:
                detail = f"redirected to {resp.headers.get('location', '?')}"
            else:
                detail = page_text(resp.text)[:300]
            raise AadError(resp.status_code, detail, url=url)
        logger.info("aad GET %s (%s live requests this session)", page, self.live_calls)
        return resp.text


async def _only_aad(request: httpx.Request) -> None:
    """Refuse a request to any host but AAD, before it is sent."""
    if request.url.host != AAD_HOST:
        raise HostNotAllowed(
            f"refusing a request to {request.url.host!r}: the AAD client only reads {AAD_HOST}"
        )


def _query(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Query pairs in a stable order; a list value becomes a repeated parameter."""
    pairs: list[tuple[str, str]] = []
    for name in sorted(params):
        value = params[name]
        if value is None:
            continue
        for item in value if isinstance(value, list) else [value]:
            pairs.append((name, str(item)))
    return pairs


def page_url(page: str, params: dict[str, Any]) -> str:
    """The full address of an AAD page, as a person could open it."""
    query = urlencode(_query(params))
    return f"{AAD_BASE}{page}" + (f"?{query}" if query else "")


def record_url(file_id: str, record_id: str) -> str:
    """The address of one record's full display."""
    return page_url("record-detail.jsp", {"dt": file_id, "rid": record_id})


# --------------------------------------------------------------------------- #
# Parsing. AAD's pages are generated by JSP templates, so their markup is
# regular; each parser looks for the landmarks it needs and raises
# AadLayoutError when one is missing rather than returning an empty answer
# that would read as "nothing found". Shapes captured 2026-10-05.
# --------------------------------------------------------------------------- #
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def clean(fragment: str) -> str:
    """Text of an HTML fragment, entities decoded and whitespace collapsed."""
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def page_text(page: str) -> str:
    """The readable text of a whole page, for error messages."""
    body = _SCRIPT.sub(" ", _COMMENT.sub(" ", page))
    return clean(body)


def _number(text: str) -> int | None:
    digits = text.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def is_please_wait(page: str) -> bool:
    """True for AAD's interim page that refreshes itself while a search runs."""
    head = page[:2000]
    return "<title>Please Wait" in head and "Refresh" in head


_SERIES_HEAD = re.compile(r'<td class="series-title">(.*?)</td>', re.S)
_SERIES_LINK = re.compile(r"series-description\.jsp\?s=(\d+)")
_SERIES_ROWS = re.compile(r'<td[^>]*class="text-right"[^>]*><b>([\d,]+)</b>')
_FILE_ROW = re.compile(
    r'<td class="file-unit"[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a href="fielded-search\.jsp\?dt=(\d+)'
    r'[^"]*">.*?</a>\s*</td>\s*<td[^>]*>([\d,]+)</td>',
    re.S,
)
_HOLDER = re.compile(
    r'<span class="[^"]*tool-tip"[^>]*title="([^"]*)"[^>]*>\s*<i>([^<]+)</i>', re.S
)


def _holder(fragment: str) -> str | None:
    """The record group or collection a header names, with its title.

    AAD prints "Record Group 47" and puts "Records of the Social Security
    Administration" in a tooltip; the citation wants both.
    """
    match = _HOLDER.search(fragment)
    if not match:
        return None
    label, name = clean(match.group(2)), clean(match.group(1))
    return f"{label}: {name}" if name else label


def parse_series_list(page: str) -> dict:
    """Read a ``series-list.jsp`` page: each series with its files.

    Returns
    -------
    dict
        ``{"series": [...]}``, each series with ``series_id``, ``title``,
        ``dates``, ``record_group``, ``rows`` and ``files`` (each with
        ``file_id``, ``title`` and ``rows``).
    """
    heads = list(_SERIES_HEAD.finditer(page))
    if not heads:
        raise AadLayoutError("no series found on the series list page")
    series = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(page)
        block = page[head.start() : end]
        link = _SERIES_LINK.search(block)
        if not link:
            continue
        heading = clean(_HOLDER.sub(" ", head.group(1)))
        heading = re.sub(r"\s*-\s*$", "", heading)
        title, _, dates = heading.partition(", created")
        dates = dates.lstrip(" ,")
        rows = _SERIES_ROWS.search(block)
        files: dict[str, dict] = {}
        for match in _FILE_ROW.finditer(block):
            file_id = match.group(2)
            # Each file is listed twice, in the short and the full list.
            files.setdefault(
                file_id,
                {
                    "file_id": file_id,
                    "title": clean(match.group(1)),
                    "rows": _number(match.group(3)),
                },
            )
        series.append(
            {
                "series_id": link.group(1),
                "title": title.strip(),
                "dates": f"created {dates}" if dates else None,
                "record_group": _holder(head.group(1)),
                "rows": _number(rows.group(1)) if rows else None,
                "files": list(files.values()),
            }
        )
    return {"series": series}


#: The header every search and record page carries. It ends at the series'
#: "(info)" link, which is the one landmark all three page types share.
_HEADER = re.compile(r"<b>File [Uu]nit:</b>(.*?)<b>in the Series:</b>(.*?\(info\)</a>)", re.S)
_DISCLAIMER = re.compile(r'<p id="disclaimer">(.*?)</p>', re.S)


def parse_header(page: str) -> dict:
    """The file, series and record group a search or record page belongs to."""
    match = _HEADER.search(page)
    if not match:
        raise AadLayoutError("the page names no file unit and series")
    file_part, series_part = match.groups()
    series_text = clean(_HOLDER.sub(" ", re.sub(r"<a\b.*?</a>", " ", series_part, flags=re.S)))
    series_text = re.sub(r"[\s.-]+$", "", series_text)
    title, _, dates = series_text.partition(", created")
    dates = dates.strip(" ,.")
    series_id = _SERIES_LINK.search(series_part)
    notice = _DISCLAIMER.search(page)
    return {
        "file": clean(file_part),
        "series": title.strip(),
        "series_dates": f"created {dates}" if dates else None,
        "series_id": series_id.group(1) if series_id else None,
        "record_group": _holder(series_part),
        "notice": clean(notice.group(1)) if notice else None,
    }


_FIELD = re.compile(
    r"popup-column-detail\.jsp\?c_id=(\d+)&(?:amp;)?dt=\d+[^']*','column_detail'[^>]*>"
    r"([^<]+)</a>(.*?)</tr>",
    re.S,
)

#: AAD's operator codes, read from the fielded search form.
OP_ALL_WORDS = "0"
OP_EQUALS = "3"
OP_BETWEEN = "8"


def parse_search_form(page: str) -> dict:
    """Read a ``fielded-search.jsp`` page: the fields a file can be searched by.

    Returns
    -------
    dict
        The header from :func:`parse_header`, ``columns`` (the default
        display columns, as AAD's ``sc`` value) and ``fields``: each with its
        ``name``, ``column`` id, AAD's ``nfo`` descriptor and a ``kind`` of
        ``text``, ``number`` or ``coded``.
    """
    out = parse_header(page)
    columns = re.search(r'name="sc" value="([^"]*)"', page)
    if not columns:
        raise AadLayoutError("the fielded search form has no column list")
    fields = []
    for column, name, rest in _FIELD.findall(page):
        nfo = re.search(rf'name="nfo_{column}" value="([^"]*)"', rest)
        if not nfo:
            continue
        if f'name="txt_{column}"' in rest:
            kind = "number" if nfo.group(1).startswith("N,") else "text"
        elif f'name="cl_{column}"' in rest:
            kind = "coded"
        else:
            kind = "other"
        fields.append({"name": name.strip(), "column": column, "nfo": nfo.group(1), "kind": kind})
    if not fields:
        raise AadLayoutError("the fielded search form lists no fields")
    out["columns"] = columns.group(1)
    out["fields"] = fields
    return out


_FOUND = re.compile(
    r"You found <b>\s*([\d,]+)\s*partial records?\s*</b>\s*out of ([\d,]+) total", re.S
)
_PAGES = re.compile(r"Page (\d+) of (\d+)")
_RESULTS = re.compile(r'<table id="queryResults"[^>]*>(.*?)</table>', re.S)
#: One result row. A free-text match outside the displayed columns spans a
#: second row, saying which field matched.
_RESULT_ROW = re.compile(
    r'<tr[^>]*>\s*<td[^>]*style="text-align: center;"[^>]*>\s*<a href="record-detail\.jsp\?'
    r'[^"]*rid=(\d+)[^"]*">.*?</a>\s*</td>(.*?)</tr>'
    r'(?:\s*<tr>\s*<td colspan="\d+">(.*?)</td>\s*</tr>)?',
    re.S,
)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def parse_results(page: str) -> dict:
    """Read a ``display-partial-records.jsp`` page.

    Returns
    -------
    dict
        The header from :func:`parse_header`, ``found`` (matches),
        ``file_rows`` (records in the file), ``page`` and ``pages``, and
        ``records``: each a dict of the displayed columns plus its
        ``record_id``, empty values left out.
    """
    out = parse_header(page)
    found = _FOUND.search(page)
    if not found:
        raise AadLayoutError("the results page does not say how many records matched")
    out["found"] = _number(found.group(1))
    out["file_rows"] = _number(found.group(2))
    pages = _PAGES.search(page)
    out["page"], out["pages"] = (
        (int(pages.group(1)), int(pages.group(2))) if pages else (1, 1 if out["found"] else 0)
    )
    records = []
    table = _RESULTS.search(page)
    if table:
        head = table.group(1).split("</tr>", 1)[0]
        columns = [clean(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)][1:]
        for record_id, cells, matched in _RESULT_ROW.findall(table.group(1)):
            values = [clean(c) for c in _CELL.findall(cells)]
            row = {"record_id": record_id}
            row.update({name: value for name, value in zip(columns, values, strict=False) if value})
            if matched := clean(re.sub(r"<br\s*/?>", "; ", matched)):
                row["matched_in"] = matched
            records.append(row)
    if out["found"] and not records:
        raise AadLayoutError(f"AAD reported {out['found']} matches but no rows could be read")
    out["records"] = records
    return out


_RECORD_ROW = re.compile(
    r"popup-column-detail\.jsp\?c_id=\d+[^']*','column_detail'[^>]*>([^<]+)</a>\s*</td>"
    r"\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
    re.S,
)


def parse_record(page: str) -> dict:
    """Read a ``record-detail.jsp`` page: every field of one record.

    Returns
    -------
    dict
        The header from :func:`parse_header` and ``fields``: each with its
        ``field`` name and ``value``, and the code's ``meaning`` where it
        differs from the value. Empty fields are left out; a record id AAD
        does not hold comes back with every field empty, so ``fields`` is
        then empty too.
    """
    out = parse_header(page)
    rows = _RECORD_ROW.findall(_COMMENT.sub(" ", page))
    if not rows:
        raise AadLayoutError("the record page lists no fields")
    fields = []
    for name, value, meaning in rows:
        value, meaning = clean(value), clean(meaning)
        if not value and not meaning:
            continue
        entry = {"field": name.strip(), "value": value}
        if meaning and meaning != value:
            entry["meaning"] = meaning
        fields.append(entry)
    out["fields"] = fields
    return out


def citation(record: dict) -> str:
    """Cite one AAD record the way NARA recommends.

    NARA's guidance (the AAD Getting Started Guide, "How do I cite
    electronic records?"): give the file, series and record group, and add in
    brackets the date the record was retrieved from AAD.
    """
    series = ", ".join(p for p in (record.get("series"), record.get("series_dates")) if p)
    parts = [record.get("file"), series, record.get("record_group")]
    where = "; ".join(p for p in parts if p)
    return (
        f"{where}. National Archives. [Retrieved from the Access to Archival "
        f"Databases (AAD), {record.get('url')}, {record.get('retrieved')}.]"
    )
