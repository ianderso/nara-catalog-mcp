# nara-catalog-mcp

[![CI](https://github.com/ianderso/nara-catalog-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ianderso/nara-catalog-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/nara-catalog-mcp)](https://pypi.org/project/nara-catalog-mcp/)

An [MCP](https://modelcontextprotocol.io) server over the **US National
Archives Catalog API**. Search NARA's holdings, read a record's description,
read what machines and volunteers have transcribed from it, and fetch its page
images.

Nothing here writes to the Catalog. The Catalog describes what the Archives
hold; a search hit is a lead, and the page images are the evidence. Nothing
here writes to a genealogy tree either — pair it with a tree server if that is
what you are doing.

This is an independent project. It is not affiliated with, endorsed by, or
supported by the National Archives and Records Administration.

## Tools

The server publishes 18 tools. Seventeen only read, and are annotated
read-only for the client. One, `download_page_image`, writes a new file to
local disk; it never overwrites one.

**Finding a record**

| Tool | Purpose |
| --- | --- |
| `search_records` | Search by title or full text. Returns totals plus compact summaries: NAID, hierarchy, holding unit, date coverage, image count. |
| `search_records_advanced` | The same search with the filters that narrow a common name: date range, record group, collection, ancestor NAID, level of description, microfilm publication number, local identifier, any control number, Congress number, reference unit, creator, person or organisation, place, type of materials, recurring date, digitised-only, "has transcriptions/tags/comments", exact matching of identifiers, `search_after` cursor paging past 10,000 hits, and each hit's OCR text (NARA's and partners') folded in on request. |
| `browse_children` | The immediate children of a node: record group to series to file unit to item. Walk down from a series you trust. |

**Reading a record**

| Tool | Purpose |
| --- | --- |
| `get_record` | Read one record in full by NAID: scope and content note, hierarchy, reference units, every page image with its object id. |
| `get_record_images` | Page images in page order, each with its object id and URL — the evidence to read before citing. |
| `download_page_image` | Save one page to a new local file, by page number or by the object id an OCR or transcription entry names. The media host is open, so this spends no API quota. |
| `get_online_availability` | Where else the record is published online, including by commercial partners. |
| `get_partner_digital_objects` | Digital object ids a commercial partner (Ancestry among them) has matched to this NAID. An empty list is the common answer. |

**What machines and other people read in it**

Everything in this group is somebody else's reading of the document. It tells
you which page to open. It does not tell you what the page says.

| Tool | Purpose |
| --- | --- |
| `get_extracted_text` | OCR text extracted from the page images, per digital object, with who produced it, and the object total and next results page when a file runs longer than one call. |
| `get_transcriptions` | Citizen transcriptions — how you get into a handwritten pension file that OCR cannot touch. |
| `get_tags` | Citizen tags, which on genealogical records are usually the names of the people inside the file. |
| `get_comments` | Other researchers' notes on the record. |

**Searching the documents rather than the catalogue**

| Tool | Purpose |
| --- | --- |
| `search_by_contribution_text` | Search transcriptions, tags, comments and OCR text — and get full record summaries back. |
| `search_transcriptions` | Find records whose transcribed text mentions a name or phrase. |
| `search_tags` | Find records carrying a tag, exactly or by word. |
| `search_extracted_text` | Find records whose partner-contributed extracted text matches. |
| `search_comments` | Find records other researchers have commented on. |

**Housekeeping**

| Tool | Purpose |
| --- | --- |
| `api_budget` | Live calls and cache hits this session, the month's live calls from a ledger beside the cache, and what remains of the key's monthly allowance. |

## Setup

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and a Catalog
API key. Request a key from the Catalog API team via
[the API documentation](https://www.archives.gov/research/catalog/help/api);
the default allowance is 10,000 calls per month.

There are two ways to run the server.

**Without cloning.** `uvx` fetches it from PyPI and runs it in one step, and
caches the result:

```bash
NARA_API_KEY=your-key uvx nara-catalog-mcp
```

**From a clone**, which is what you want if you will change it:

```bash
git clone https://github.com/ianderso/nara-catalog-mcp
cd nara-catalog-mcp
uv sync
cp .env.example .env      # then put your key in it
uv run nara-catalog-mcp   # stdio server, usually launched by the client
```

Either way the server speaks MCP over stdio, so you will normally let an MCP
client start it rather than run it by hand.

### Claude Desktop

Without cloning:

```json
{
  "mcpServers": {
    "nara": {
      "command": "uvx",
      "args": ["nara-catalog-mcp"],
      "env": { "NARA_API_KEY": "your-key" }
    }
  }
}
```

From a clone (the `env` block can be dropped if the key is in the clone's
`.env`):

```json
{
  "mcpServers": {
    "nara": {
      "command": "uv",
      "args": ["--directory", "/path/to/nara-catalog-mcp", "run", "nara-catalog-mcp"],
      "env": { "NARA_API_KEY": "your-key" }
    }
  }
}
```

A desktop app does not always inherit your shell's `PATH`. If the server fails
to start because `uv` or `uvx` cannot be found, give the full path that
`which uvx` prints as the `command`.

### Claude Code

```bash
claude mcp add nara --env NARA_API_KEY=your-key -- uvx nara-catalog-mcp
```

or, from a clone whose `.env` holds the key:

```bash
claude mcp add nara -- uv --directory /path/to/nara-catalog-mcp run nara-catalog-mcp
```

## Configuration

All settings come from the environment. A `.env` file in the directory the
server starts in supplies any that the environment does not; with
`uv --directory` that is the clone. Only that directory is read — not its
parents, and not the directory the package is installed in.

| Variable | Meaning |
| --- | --- |
| `NARA_API_KEY` | Your Catalog API key. Required. |
| `NARA_CACHE_DIR` | Response cache directory. Default `~/.cache/nara-catalog-mcp`. |
| `NARA_TIMEOUT` | HTTP timeout in seconds. Default 60. |
| `NARA_MONTHLY_CALL_BUDGET` | Calls per month the key allows, reported by `api_budget`. Default 10000. |

A missing key, or an unusable value, is reported on the first tool call as a
`not_configured` result naming the variable.

## The call budget

A Catalog key is capped per month, and a sweep across many names will exhaust
it faster than expected. Every successful response is cached on disk, keyed by
endpoint and query, so repeating a call costs nothing. Live calls are also
written to a ledger, `budget.json` beside the cache, so `api_budget` reports
the month's spend across sessions as well as this session's live calls and
cache hits. A rejected call counts as live, since it reached the API. The
ledger sees one machine and one cache directory; the Catalog's own count is
the authority.

The cache never expires. Pass `refresh=true` to `get_record`,
`get_record_images`, `get_extracted_text`, `get_transcriptions`, `get_tags`
or `get_comments` to re-read one answer from the Catalog: that spends one
call and replaces the cached copy. New transcriptions and tags arrive over
time, and a file that was paper-only can be digitised, so a cached "nothing
here" is the answer most worth refreshing. Deleting `NARA_CACHE_DIR` still
works, and also resets the month's ledger.

## Searching past the first 10,000 hits

`page` stops working beyond 10,000 results, which a common surname passes.
`search_records_advanced` reports a `paging_note` when you hit that boundary;
either narrow the search or re-run it with `search_after="*"` and follow the
`next_search_after` value from each response.

## How a search behaves

`title` matches words in the record title and is the precise option — case
files are usually titled with the person's name. `query` searches the full
description: broader, noisier, and worth reaching for only when a title search
finds nothing.

Results come back as summaries rather than whole records. A raw Catalog record
is large and mostly irrelevant to the decision you are making, which is whether
this record is worth opening.

## Notes

- **A description is not evidence.** It summarises a file; it says nothing
  about what any individual page contains. Read the images before citing.
- **Neither is OCR, and neither is a transcription.** `get_extracted_text` is
  a machine reading a scan of handwriting; `get_transcriptions` is one
  volunteer's typing, unreviewed. Both are the fastest way to find the page
  that matters, and neither is a source. Open the image and cite that.
- **Record the NAID.** It is the stable identifier that makes a citation
  refindable.
- **Not every record is digitised.** `image_count: 0` means the description
  exists but the pages are not online — the `reference_units` field tells you
  which archive holds the paper.
- **Open is not unrestricted.** The media host needs no key, and most of
  NARA's holdings are in the public domain, but not all: some descriptions
  record use restrictions. Check before republishing an image.

## Deliberately not here

The Catalog API can post tags and comments and put transcriptions. This server
does not, and will not by default: a contribution publishes under whoever's key
is configured, and nothing here can take it back.

## Security

- **The key** is read from the environment, sent only to
  `catalog.archives.gov` as the `x-api-key` header, and never sent to the
  media host. It is not written to the response cache.
- **Contributions are untrusted text.** Tags, comments and transcriptions are
  written by members of the public and reach the model verbatim, so one could
  contain instructions aimed at it. The server's instructions tell the model to
  treat that text as material to weigh, never as instructions; the model is
  still the one deciding, so review what it proposes to do.
- **`download_page_image` writes files.** It creates a new file wherever the
  server's user can write, and refuses to overwrite an existing one — so a
  mistaken or injected path cannot destroy anything. It is annotated as not
  read-only, so a client can ask before each call.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --extra dev
uv run pytest                # mocked with respx; no API key needed
uv run ruff check .
uv run ruff format --check .
uv run python -m tests.live_check   # needs a key: asks the Catalog what the mocks cannot
```

The live check asks the Catalog what the mocks cannot: where the two text
flags put their text, and which search parameters the server does not send.
Run it after a change on either side; a difference shows up in its output.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how the suite is organised and what
a change is expected to carry.

## API notes

The published OpenAPI spec is unreliable in specific, repeatable ways — wrong
`required` flags, `maximum` used where `maxLength` is meant, and two
conflicting definitions of the pagination cursor. Corrections verified against
the live Catalog are in [docs/API-NOTES.md](docs/API-NOTES.md). Why the server
is shaped the way it is, what is out of scope by decision, and how the test
suite is built are in [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE).
