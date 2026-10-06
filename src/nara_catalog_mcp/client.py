"""Async HTTP client for the National Archives Catalog API v2.

Most of the work goes through ``GET /api/v2/records/search``: a record is
retrieved by passing its ``naId`` as a search parameter rather than through a
separate detail route. The contribution, extracted-text, hierarchy and
availability routes are plain GETs on their own paths, so :meth:`NaraClient.get`
takes the path and :meth:`NaraClient.search` is a thin wrapper over it.

Responses are cached on disk keyed by path *and* query. The key carries a
monthly call quota, so a cache hit is worth more here than latency alone
suggests; the client counts live calls in memory for the session and in a
small ledger beside the cache for the month, so
:func:`~nara_catalog_mcp.server.api_budget` can report the remaining headroom.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("nara_catalog_mcp.client")

#: Base URL of the Catalog API.
API_BASE = "https://catalog.archives.gov/api/v2"

#: Field set worth requesting for a search hit. The full record is large, and
#: ``sourceIncludes`` is the documented way to trim it. The creator fields are
#: what tell apart series that share a title; nested paths such as
#: ``ancestors.creators.heading`` select as expected (verified live 2026-10-05).
LEAN_FIELDS = (
    "naId,title,levelOfDescription,recordType,"
    "creators.heading,creators.creatorType,"
    "ancestors.naId,ancestors.title,ancestors.levelOfDescription,"
    "ancestors.creators.heading,ancestors.creators.creatorType,"
    "digitalObjects.objectUrl,digitalObjects.objectType,"
    "physicalOccurrences.referenceUnits.name,"
    "scopeAndContentNote,coverageStartDate,coverageEndDate,onlineResources.url"
)

#: Name of the per-month call ledger inside the cache directory. Cache
#: entries are named by a 20-character hex digest, so the two cannot collide.
BUDGET_FILE = "budget.json"


def current_month() -> str:
    """The calendar month the key's quota is counted in, as ``YYYY-MM``.

    Counted in UTC. The Catalog's gateway resets its quota on a clock this
    server cannot see; if that clock is not UTC the boundary is off by
    hours, not days.
    """
    return datetime.now(UTC).strftime("%Y-%m")


class NaraApiError(RuntimeError):
    """Raised on a non-2xx response, carrying status and server detail."""

    def __init__(self, status: int, detail: str, *, path: str):
        """Record the failing request and the server's explanation."""
        self.status = status
        self.detail = detail
        super().__init__(f"GET {path} -> {status}: {detail}")


class NaraClient:
    """Cached async client for one Catalog API key.

    Usable as an async context manager, which closes the transport on exit.

    Parameters
    ----------
    api_key : str
        Catalog API key, sent as the ``x-api-key`` header.
    cache_dir : Path
        Directory for cached responses. Created on first write.
    timeout : float, optional
        Per-request HTTP timeout in seconds.
    """

    def __init__(self, api_key: str, cache_dir: Path, *, timeout: float = 60.0):
        self._key = api_key
        self._cache_dir = cache_dir
        self._http = httpx.AsyncClient(base_url=API_BASE, timeout=timeout)
        self._live_calls = 0
        self._cache_hits = 0

    @property
    def live_calls(self) -> int:
        """int: Requests sent to the API this session, cache hits excluded.

        A request that came back as an error is counted: it reached the
        Catalog and spent quota all the same.
        """
        return self._live_calls

    def month_ledger(self) -> tuple[str, int]:
        """The current month and the live calls recorded against it.

        Read from ``budget.json`` in the cache directory on every call, so a
        second server sharing the directory is seen as soon as it writes.
        A missing, corrupt or last-month ledger reads as zero.

        Returns
        -------
        tuple of (str, int)
            The month as ``YYYY-MM`` in UTC, and the count.
        """
        month = current_month()
        try:
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return month, 0
        if not isinstance(data, dict) or data.get("month") != month:
            return month, 0
        count = data.get("live_calls")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return month, 0
        return month, count

    @property
    def _ledger_path(self) -> Path:
        return self._cache_dir / BUDGET_FILE

    def _record_live_call(self) -> None:
        """Count one request against this session and this month's ledger.

        The ledger is read, incremented and replaced atomically, so a reader
        never sees a torn file. Two servers sharing one cache directory can
        still read the same value and both write it plus one, losing a count
        per collision. That undercount is accepted for a personal key, and
        ``api_budget`` says so.
        """
        self._live_calls += 1
        month, count = self.month_ledger()
        ledger = self._ledger_path
        try:
            _write_atomically(
                ledger,
                json.dumps(
                    {
                        "month": month,
                        "live_calls": count + 1,
                        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                ),
            )
        except OSError:
            # The session count still works; only this one call is missing
            # from the month's figure.
            logger.warning("could not update the call ledger at %s", ledger)

    @property
    def cache_hits(self) -> int:
        """int: Requests served from the on-disk cache this session."""
        return self._cache_hits

    async def aclose(self) -> None:
        """Close the underlying HTTP transport."""
        await self._http.aclose()

    async def __aenter__(self) -> NaraClient:
        """Return the client."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the transport."""
        await self.aclose()

    def _cache_path(self, path: str, query: str) -> Path:
        """Path of the cache entry for one endpoint path and query string.

        The path is part of the key: ``/tags/naId/1`` and ``/comments/naId/1``
        take the same parameters and must not share an entry.
        """
        digest = hashlib.sha256(f"{path}?{query}".encode()).hexdigest()[:20]
        return self._cache_dir / f"{digest}.json"

    async def get(self, path: str, *, refresh: bool = False, **params: Any) -> Any:
        """Fetch one Catalog endpoint, serving from the cache when possible.

        Parameters
        ----------
        path : str
            API path below the base URL, e.g. ``/tags/naId/12345``. Any NAID
            it carries must already be substituted in.
        refresh : bool, optional
            Skip the cached copy and ask the Catalog again, spending one
            call; the fresh answer replaces the cached one. The cache never
            expires on its own, so this is the only way to see a change.
        **params
            Query parameters. ``None`` values are dropped; ``False`` is kept,
            because ``tags_exist=false`` is a real filter.

        Returns
        -------
        Any
            The decoded response body. Usually a dict -- the search envelope,
            or a route-specific object such as ``/extractedText``'s paginated
            ``digitalObjects`` -- though some routes answer with a list.

        Raises
        ------
        NaraApiError
            On any status of 400 or above. The response is not cached, so a
            transient failure does not poison the next attempt. The call is
            still counted: it reached the Catalog and spent quota.
        """
        clean = {k: v for k, v in params.items() if v is not None}
        query = urlencode(sorted(clean.items()))

        cached = self._cache_path(path, query)
        if cached.exists() and not refresh:
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                # A truncated or hand-edited entry is a re-fetch, not a crash.
                logger.warning("discarding corrupt cache entry %s", cached.name)
            else:
                self._cache_hits += 1
                return data

        resp = await self._http.get(
            path,
            params=clean,
            headers={"x-api-key": self._key, "Content-Type": "application/json"},
        )
        # Counted before the status check: a rejected request still reached
        # the API, and an undercount is the wrong way for a budget to err.
        self._record_live_call()
        if resp.status_code >= 400:
            raise NaraApiError(resp.status_code, _detail(resp), path=path)

        data = resp.json()
        _write_atomically(cached, json.dumps(data))
        logger.info("catalog GET %s (%s live calls this session)", path, self._live_calls)
        return data

    async def search(self, *, refresh: bool = False, **params: Any) -> dict:
        """Query the records search endpoint.

        Parameters
        ----------
        refresh : bool, optional
            Bypass the cache for this one query; see :meth:`get`.
        **params
            Query parameters. ``naId`` fetches one record; ``title`` searches
            titles; ``q`` searches full text. ``limit`` and ``page`` paginate,
            ``searchAfter`` pages past 10,000 hits. None values are dropped.

        Returns
        -------
        dict
            The decoded response body.

        Raises
        ------
        NaraApiError
            On any status of 400 or above.
        """
        return await self.get("/records/search", refresh=refresh, **params)


#: Leading bytes of the media formats the Catalog serves as digital objects.
_SIGNATURES = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"%PDF-", "pdf"),
)


def sniff_format(head: bytes) -> str:
    """Name a file's format from its first bytes, or ``"unknown"``.

    The bytes are trusted over the URL's extension: an HTML error page
    served with a 200 would otherwise pass for a successful download.
    """
    for signature, name in _SIGNATURES:
        if head.startswith(signature):
            return name
    return "unknown"


async def download(url: str, target: Path, *, timeout: float = 120.0) -> tuple[int, str]:
    """Stream a digital object from the Catalog's media host to a new file.

    The media URLs are open: verified live 2026-09-23 that an ``objectUrl``
    answers 200 with no ``x-api-key`` and no redirect. The key is deliberately
    not sent -- it is not needed, and a media fetch is not an API call, so it
    must not spend quota.

    The body is streamed to disk rather than held in memory, because a
    digital object is not always a page scan: records also carry PDFs,
    audio and video, far larger than any page.

    Parameters
    ----------
    url : str
        An ``objectUrl`` from a record's digital objects.
    target : Path
        The file to create. It must not exist: an existing file is never
        overwritten, so a mistaken path cannot destroy anything.
    timeout : float, optional
        Timeout in seconds for connecting and for each read, not for the
        whole transfer.

    Returns
    -------
    tuple of (int, str)
        Bytes written, and the format named by :func:`sniff_format`.

    Raises
    ------
    NaraApiError
        On any status of 400 or above. Nothing is written.
    FileExistsError
        If ``target`` already exists. It is left untouched.
    """
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http,
        http.stream("GET", url, headers={"Accept": "*/*"}) as resp,
    ):
        if resp.status_code >= 400:
            await resp.aread()
            raise NaraApiError(resp.status_code, _detail(resp), path=url)

        # Exclusive creation fails, before anything is written, if the file
        # exists -- including one created since the caller last checked.
        out = target.open("xb")
        written, head = 0, b""
        try:
            with out:
                async for chunk in resp.aiter_bytes():
                    if len(head) < 16:
                        head += chunk[: 16 - len(head)]
                    out.write(chunk)
                    written += len(chunk)
        except BaseException:
            # A partial file would pass for a finished download. This call
            # created it, so this call may remove it.
            target.unlink(missing_ok=True)
            raise
    return written, sniff_format(head)


def _write_atomically(path: Path, text: str) -> None:
    """Write a file so no reader ever sees half of it.

    A plain write interrupted part-way -- the client closing the server, say
    -- leaves a truncated entry. The cache reader tolerates one, but it costs
    a call to replace, and the ledger reader would count the month from zero.
    Writing beside the target and renaming over it means the file is either
    the old one, the new one, or absent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _detail(resp: httpx.Response) -> str:
    """Pull a human-readable explanation out of an error response body."""
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("message") or body.get("error") or body)
        return str(body)
    except Exception:
        return resp.text[:300]


def unwrap(payload: dict) -> tuple[int, list[dict]]:
    """Split a search response into a total and the records it carried.

    The API nests results as ``body.hits.hits[]._source.record``, and some
    deployments omit the outer ``body``.

    Parameters
    ----------
    payload : dict
        A search response body.

    Returns
    -------
    tuple of (int, list of dict)
        Total matches the API reports, and the records on this page.
    """
    hits = _hits(payload)
    total = hits.get("total")
    if isinstance(total, dict):
        # Elasticsearch 7 and later: {"value": n, "relation": "eq"}.
        total = total.get("value")
    # Older Elasticsearch reports a bare integer; anything else is unknown.
    return (total if isinstance(total, int) else 0), _sources(hits)


def unwrap_any(payload: Any) -> list[dict]:
    """Best-effort list of objects from any Catalog response shape.

    The search routes nest results under ``body.hits.hits``, the contribution
    routes reuse that envelope, and ``/extractedText`` returns a paginated
    object carrying ``digitalObjects``. This flattens them all so a caller
    does not have to ask which it got.

    Verified against the live Catalog on 2026-09-23.

    Parameters
    ----------
    payload : Any
        A decoded response body.

    Returns
    -------
    list of dict
        The objects the response carried, in the order given.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if _hits(payload):
        return _sources(_hits(payload))
    body = payload.get("body", payload)
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    for key in ("digitalObjects", "results", "data", "records"):
        if isinstance(body.get(key), list):
            return [item for item in body[key] if isinstance(item, dict)]
    return [body] if body else []


def cursor(payload: dict) -> str | None:
    """Return the ``searchAfter`` cursor for the page after this one.

    The API asks for the ``sort`` array of the last hit, passed back as the
    next request's ``searchAfter``. Without it, paging stops at 10,000 hits.

    Parameters
    ----------
    payload : dict
        A search response body.

    Returns
    -------
    str or None
        The cursor value, or None when the page carried no sortable hit.
    """
    raw = _hits(payload).get("hits") or []
    if not raw or not isinstance(raw[-1], dict):
        return None
    sort = raw[-1].get("sort")
    if isinstance(sort, list):
        return ",".join(str(v) for v in sort) or None
    return str(sort) if sort is not None else None


def _hits(payload: Any) -> dict:
    """The ``hits`` object of a search-shaped response, or an empty dict."""
    if not isinstance(payload, dict):
        return {}
    body = payload.get("body", payload)
    hits = body.get("hits") if isinstance(body, dict) else None
    return hits if isinstance(hits, dict) else {}


def _sources(hits: dict) -> list[dict]:
    """Pull the objects out of ``hits.hits[]._source``.

    Description searches wrap the object one level deeper, in ``_source.record``;
    some contribution routes do not. Accept both rather than return nothing.
    """
    out = []
    for hit in hits.get("hits") or []:
        if not isinstance(hit, dict) or "_source" not in hit:
            continue
        source = hit["_source"]
        if not isinstance(source, dict):
            continue
        record = source.get("record", source)
        out.append(record if isinstance(record, dict) else source)
    return out
