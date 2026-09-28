# Contributing

Issues and pull requests are welcome. This file says how the project is put
together and what a change is expected to carry.

## Setting up

```bash
git clone https://github.com/ianderso/nara-catalog-mcp
cd nara-catalog-mcp
uv sync --extra dev
```

Before sending a change, run what CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The suite is mocked with [respx](https://lundberg.github.io/respx/) and needs
no API key. It must never touch the live Catalog: a contributor's key has a
monthly cap, and CI has no key at all.

## Where things live

| Path | What it holds |
| --- | --- |
| `src/nara_catalog_mcp/server.py` | The tools. Their docstrings and `Field` descriptions *are* the published tool descriptions and schema. |
| `src/nara_catalog_mcp/client.py` | The cached HTTP client, response unwrapping, and the media download. |
| `src/nara_catalog_mcp/shape.py` | Turning raw, uneven Catalog records into compact results. |
| `src/nara_catalog_mcp/config.py` | Settings from the environment and `.env`. |
| `docs/API-NOTES.md` | Where the API's published spec and its real behaviour disagree. |
| `docs/DESIGN.md` | Why the server is shaped the way it is, and what is out of scope by decision. |
| `tests/test_tool_contract.py` | Tests over the tool surface as a client sees it. |
| `tests/live_check.py` | The one script that talks to the live Catalog, run by hand with a key. Not collected. |

## What a change carries

**A test that fails without it.** Bug fixes especially: reproduce the bug as a
test first.

**Descriptions written for the model.** A tool's docstring is what a model
reads when choosing and calling it, so write it for that reader, not for a
developer. The combined descriptions have a ceiling
(`DESCRIPTION_BUDGET` in `tests/test_tool_contract.py`), because they are sent
on every session before any work happens. Raise it deliberately, in its own
commit, saying why.

**The evidence distinction, kept.** OCR text, transcriptions, tags and
comments are someone else's reading of a document. Any tool returning them
must say in its description that it is not evidence and point back at the
page image; a contract test enforces this.

**A structured result, never an exception.** Every tool catches its failures
and returns an `error` envelope. A sweep test calls every tool with the API
failing and fails if one raises.

**A snapshot update, if the surface changed.** Renaming or adding a tool or
parameter changes what callers depend on, so it has to show up as a diff:

```bash
uv run python -m tests.regen_tool_snapshot
```

Commit the regenerated `tests/fixtures/tool_schema.json` with the change, and
add the tool to the README's tables — a test checks that too.

**Dated evidence for claims about the live API.** The published spec is wrong
often enough that behaviour is only trusted once seen. When a change depends
on how the live Catalog answers, record what was observed and when, in
`docs/API-NOTES.md` and beside the code or fixture that relies on it. Remove
personal names and anything else identifying from captured payloads before
committing them. `uv run python -m tests.live_check`, with a key in `.env`,
re-checks the claims the notes already make and spends two calls doing it.

## What will not be merged

Tools that write to the Catalog — posting tags or comments, submitting
transcriptions. A contribution publishes under the operator's account and
cannot be withdrawn by this server. See "Out of scope, by decision" in
[docs/DESIGN.md](docs/DESIGN.md).

## Releasing

1. Update `__version__` in `src/nara_catalog_mcp/__init__.py`; the package
   version is read from there.
2. Move the changelog's entries under a heading for the new version.
3. Tag the commit `vX.Y.Z` and create a GitHub release from the tag.
