"""Live checks against the Catalog that need a real key.

Not part of the test suite: every test runs mocked. Run this by hand when
``docs/API-NOTES.md`` has a question only the live Catalog can answer::

    uv run python -m tests.live_check             # both questions, 2 calls
    uv run python -m tests.live_check --classify  # also probe each unexposed
                                                  # search parameter alone,
                                                  # one call each

It reads ``NARA_API_KEY`` the way the server does (environment, then
``.env``), uses the server's own cache directory with ``refresh`` on every
call so each answer is current and the month's ledger counts the spend, and
prints what to record in API-NOTES.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from nara_catalog_mcp.client import API_BASE, NaraApiError, NaraClient, unwrap
from nara_catalog_mcp.config import ConfigError, load_config
from nara_catalog_mcp.shape import record_extracted_text

SWAGGER = f"{API_BASE}/swagger.json"
SERVER_SOURCE = Path(__file__).resolve().parent.parent / "src" / "nara_catalog_mcp" / "server.py"

#: A digitised Revolutionary War pension file with partner OCR on every page:
#: the specimen the fixtures were captured from.
SPECIMEN = "54765873"


def long_strings(node: Any, path: str = "$", *, minimum: int = 80) -> list[tuple[str, int]]:
    """Every string of at least ``minimum`` characters in a payload, with its path.

    Parameters
    ----------
    node : Any
        A decoded JSON payload, or any part of one.
    path : str, optional
        JSONPath-style prefix for the strings found under ``node``.
    minimum : int, optional
        Shortest string worth reporting. OCR text is long; identifiers are
        not, so a threshold separates the two without knowing the keys.

    Returns
    -------
    list of (str, int)
        Each string's path and length, in document order.
    """
    found: list[tuple[str, int]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += long_strings(value, f"{path}.{key}", minimum=minimum)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += long_strings(value, f"{path}[{i}]", minimum=minimum)
    elif isinstance(node, str) and len(node) >= minimum:
        found.append((path, len(node)))
    return found


def parameters_the_server_sends(source: str | None = None) -> set[str]:
    """Names the server puts on a ``/records/search`` request, read from its source.

    Over-matches deliberately: it also picks up output keys that happen to
    share the shape. Intersect the result with the spec's parameter names
    before drawing a conclusion from it.

    Parameters
    ----------
    source : str, optional
        The text of ``server.py``. Defaults to the file on disk.

    Returns
    -------
    set of str
        Candidate parameter names.
    """
    src = SERVER_SOURCE.read_text() if source is None else source
    # "apiName": local_variable,      -- a parameter dict entry
    names = set(re.findall(r'"([A-Za-z_]+)": [a-z_]+(?: or None)?,', src))
    # "plain": "plain_is",            -- both sides of an exact-form mapping
    for key, value in re.findall(r'"([A-Za-z_]+)": "([A-Za-z_]+)"', src):
        names.update((key, value))
    # params["apiName"] = ...         -- set by name after the dict
    names |= set(re.findall(r'params\["([A-Za-z]+)"\]', src))
    # keyword arguments to client.search(...)
    names |= set(re.findall(r"\b(naId|sourceIncludes|limit|page|q|title)=", src))
    return names


def probe_value(spec: dict) -> Any:
    """A value to send a parameter on its own, chosen from its schema."""
    if spec.get("enum"):
        return spec["enum"][0]
    kind = spec.get("type") or (spec.get("schema") or {}).get("type")
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        return 1
    return "test"


def _normalise_paths(paths: list[tuple[str, int]]) -> set[str]:
    """Collapse list indices so one page and fifty report the same shape."""
    return {re.sub(r"\[\d+\]", "[*]", path) for path, _ in paths}


async def check_include_extracted_text(client: NaraClient, save: Path | None) -> None:
    """Q1: where do the two text flags put the text in a search hit?

    ``include_extracted_text`` sends both ``includeExtractedText`` (NARA's
    OCR, verified at ``digitalObjects[*].extractedText``) and
    ``includeOtherExtractedText`` (partners' text, documented at
    ``digitalObjects[*].otherExtractedText``), so both are sent here.
    """
    print("== Q1: where do includeExtractedText and includeOtherExtractedText put the text?")
    with_flag = await client.search(
        naId=SPECIMEN,
        includeExtractedText=True,
        includeOtherExtractedText=True,
        refresh=True,
    )
    without = await client.search(naId=SPECIMEN, refresh=True)
    if save:
        save.write_text(json.dumps(with_flag, indent=1))
        print(f"raw payload written to {save}")

    added = sorted(
        _normalise_paths(long_strings(with_flag)) - _normalise_paths(long_strings(without))
    )
    print(f"long strings present only with the flags ({len(added)} distinct path shapes):")
    for path in added:
        print("   ", path)
    if not added:
        print("    (none: the flags added no long text to this record)")
    objects = [
        o
        for hit in ((with_flag.get("body") or {}).get("hits") or {}).get("hits") or []
        for o in (((hit.get("_source") or {}).get("record") or {}).get("digitalObjects") or [])
        if isinstance(o, dict)
    ]
    nara = sum(1 for o in objects if o.get("extractedText"))
    partner = sum(1 for o in objects if o.get("otherExtractedText"))
    kinds = sorted(
        {type(o["otherExtractedText"]).__name__ for o in objects if o.get("otherExtractedText")}
    )
    print(
        f"digital objects: {len(objects)}; with extractedText: {nara}; "
        f"with otherExtractedText: {partner} (JSON type {kinds or 'n/a'})"
    )

    _, records = unwrap(with_flag)
    entries = record_extracted_text(records[0]) if records else []
    attributed = sum(1 for e in entries if e["contributed_by"])
    print(
        f"record_extracted_text() found {len(entries)} page(s) of text, "
        f"{attributed} attributed to a partner"
    )
    if added and not entries:
        print(
            "!!  The text is somewhere record_extracted_text does not look. "
            "Fix shape.py and record the path in API-NOTES."
        )
    elif entries:
        print("ok  shape.py reads it. Record the path above in API-NOTES as verified.")


def resolve_ref(spec: dict, ref: str) -> dict:
    """Follow a local JSON pointer such as ``#/components/parameters/q``.

    Returns an empty dict for a pointer that leads nowhere, so a broken
    reference drops one parameter rather than the whole audit.
    """
    if not ref.startswith("#/"):
        return {}
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}


def search_parameters(spec: dict, path: str = "/records/search") -> dict[str, dict]:
    """The resolved parameters of ``GET path`` in a decoded Swagger or OpenAPI spec.

    Parameters may sit on the path item or on the operation, and each may be
    a ``$ref`` into ``#/parameters`` (Swagger 2), ``#/components/parameters``
    (OpenAPI 3) or anywhere else in the document. All of it is read; an
    operation-level parameter overrides a path-level one of the same name.
    """
    item = (spec.get("paths") or {}).get(path) or {}
    if not isinstance(item, dict):
        return {}
    raw = list(item.get("parameters") or [])
    raw += list((item.get("get") or {}).get("parameters") or [])
    out: dict[str, dict] = {}
    for param in raw:
        if isinstance(param, dict) and "$ref" in param:
            param = resolve_ref(spec, param["$ref"])
        if isinstance(param, dict) and param.get("name"):
            out[param["name"]] = param
    return out


def _explain_empty_spec(spec: dict) -> None:
    """Print enough of an unrecognised spec's structure to fix the resolver."""
    print("!!  no parameters resolved from the spec; its shape is not what the resolver expects.")
    print("    top-level keys:", sorted(spec)[:15])
    item = (spec.get("paths") or {}).get("/records/search")
    print("    /records/search:", sorted(item) if isinstance(item, dict) else repr(item)[:80])
    get = item.get("get") if isinstance(item, dict) else None
    print("    get:", sorted(get) if isinstance(get, dict) else repr(get)[:80])
    raw = (get or {}).get("parameters") or (item or {}).get("parameters") if item else None
    print("    first raw parameter:", json.dumps(raw[0])[:200] if raw else repr(raw))
    print("    Re-run with --save-spec FILE and fix search_parameters() in this script.")


async def audit_parameters(
    http: httpx.AsyncClient, client: NaraClient, classify: bool, save_spec: Path | None
) -> None:
    """Q2: which ``/records/search`` parameters does the server not send?"""
    print("\n== Q2: which /records/search parameters are not exposed?")
    resp = await http.get(SWAGGER)
    resp.raise_for_status()
    full = resp.json()
    if save_spec:
        save_spec.write_text(json.dumps(full, indent=1))
        print(f"spec written to {save_spec}")
    spec = search_parameters(full)
    if not spec:
        _explain_empty_spec(full)
        return
    sent = parameters_the_server_sends() & set(spec)
    unexposed = sorted(set(spec) - sent)
    print(
        f"spec: {len(spec)} parameters; the server sends {len(sent)}; unexposed: {len(unexposed)}"
    )
    for name in unexposed:
        param = spec[name]
        kind = param.get("type") or (param.get("schema") or {}).get("type") or "?"
        desc = " ".join((param.get("description") or "").split())[:88]
        print(f"   {name:34} {kind:8} {desc}")
    if not classify:
        print(
            "(--classify sends each one alone to see whether it is a search term "
            "or a refinement; one call each)"
        )
        return

    print("\nprobing each alone (a 400 'No search terms entered' means refinement):")
    for name in unexposed:
        value = probe_value(spec[name])
        try:
            payload = await client.search(**{name: value}, limit=1, refresh=True)
            total, _ = unwrap(payload)
            print(f"   {name:34} 200  standalone  {total} hit(s) for {value!r}")
        except NaraApiError as exc:
            tag = "refinement" if "No search terms" in exc.detail else "rejected"
            print(f"   {name:34} {exc.status}  {tag:11} {exc.detail[:70]}")


async def main(argv: list[str] | None = None) -> int:
    """Run the checks. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--classify",
        action="store_true",
        help="send each unexposed search parameter alone; one call per parameter",
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="write the raw includeExtractedText payload to this file",
    )
    parser.add_argument(
        "--save-spec",
        type=Path,
        help="write the fetched swagger.json to this file",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    async with (
        NaraClient(cfg.api_key, cfg.cache_dir, timeout=cfg.timeout) as client,
        httpx.AsyncClient(timeout=cfg.timeout) as http,
    ):
        await check_include_extracted_text(client, args.save)
        await audit_parameters(http, client, args.classify, args.save_spec)
        month, month_calls = client.month_ledger()
        print(f"\n{client.live_calls} live call(s) spent; {month_calls} recorded for {month}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
