# nara-catalog-mcp

[![CI](https://github.com/ianderso/nara-catalog-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ianderso/nara-catalog-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/nara-catalog-mcp)](https://pypi.org/project/nara-catalog-mcp/)

<!-- mcp-name: io.github.ianderso/nara-catalog-mcp -->

An [MCP](https://modelcontextprotocol.io) server for genealogical research in
the **US National Archives Catalog**. Find an ancestor's pension file, service
record or census page, read what machines and volunteers have transcribed
from it, and fetch the page images you will cite. It also searches NARA's
**Access to Archival Databases** (AAD), which the Catalog cannot reach: the
NUMIDENT's Social Security applications and deaths, the WWII Army enlistment
cards, and the 1820–1912 ship passenger lists among them.

It works the way a careful genealogist does. A catalogue description is a
finding aid, and OCR text, transcriptions and tags are somebody else's
reading of the page: all of them are leads. The page image is the source. The
tools say which is which, and the server tells the model to read the image
before citing it.

Nothing here writes to the Catalog, and nothing here keeps a family tree. The
server finds and reads records; what you conclude from them belongs in your
genealogy software, or in a family-tree MCP server running alongside this one.

The tools work on any record the Catalog describes, so a historian or a
journalist can use them too. The examples throughout are about tracing people.

This is an independent project. It is not affiliated with, endorsed by, or
supported by the National Archives and Records Administration.

## Tools

The server publishes 21 tools. Twenty only read, and are annotated
read-only for the client. One, `download_page_image`, writes a new file to
local disk; it never overwrites one.

**Finding a record**

| Tool | Purpose |
| --- | --- |
| `search_records` | Search by title or full text. Returns totals plus compact summaries: NAID, hierarchy, holding unit, date coverage, image count. Each series names its creator, the office that made it, which tells apart the many series sharing a title. |
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

**NARA's Access to Archival Databases (AAD)**

AAD serves a selection of NARA's electronic records as searchable tables.
Each row is a database entry an agency clerk typed from a record: a
transcription or index entry, not the record, and cited as an electronic
record. These tools need no key and spend none of the Catalog allowance.

| Tool | Purpose |
| --- | --- |
| `aad_list_series` | The series in an AAD category (genealogy by default), each with its files and their ids. A search runs on one file. |
| `aad_search` | Search one file by free text over the whole row and by named fields; 50 rows a page, each with its record id. |
| `aad_get_record` | One row in full, every coded value with its meaning, and a citation in NARA's recommended form with the date retrieved. |

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
| `NARA_API_KEY` | Your Catalog API key. Required by the Catalog tools; the AAD tools need none. |
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

## Searching AAD

AAD has no API, so `aad_*` read its web pages the way a browser does, and
parse them. That shapes how they behave:

- **One file at a time.** A big series is split into files, and a search
  runs on one. The NUMIDENT is split by entry type (application, claim,
  death) and by surname range, so `aad_list_series` comes first.
- **Fields by name.** `fields={"LAST NAME": "PRESLEY", "DATE OF BIRTH (YEAR)":
  "1930-1935"}`. A field AAD stores as a code, such as a state or a
  country, cannot be searched by name: put its meaning in `query`, which
  matches the whole row, code meanings included.
- **Slow on purpose.** One request at a time, at least two seconds apart,
  and a search usually takes two requests because AAD answers first with
  a "Please Wait" page. Answers are cached for seven days, under
  `NARA_CACHE_DIR/aad`; `refresh=true` on `aad_get_record` fetches a row
  again.
- **Record ids are not stable.** NARA reloads files, and a reload can
  renumber the rows. Search again rather than reuse an old id, and cite a
  row by its fields, its file and its series.
- **A row is not the record.** It was keyed from an SS-5, a punch card or a
  manifest, with the keying errors that brings, and no file holds everyone.
  The NUMIDENT has entries only for people with a verified death or born
  before 1908, and its death file omits state-reported deaths. Each answer
  carries AAD's own notice for the file, and a NUMIDENT answer says what
  that file leaves out.

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
  media host or to AAD. It is not written to the response cache.
- **The AAD client reads one host.** A request hook refuses any host but
  `aad.archives.gov`, so nothing a model passes in can make it fetch another
  site. It identifies itself as `Mozilla/5.0 (compatible;
  nara-catalog-mcp/<version>; +<this repository>)`, the form crawlers such
  as Googlebot use. AAD's load balancer answered 403 to the plain
  `nara-catalog-mcp/<version>` form when it was tried; see
  [docs/API-NOTES.md](docs/API-NOTES.md).
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
flags put their text, which search parameters the server does not send, and
whether search summaries still name each series' creator. It also asks AAD
whether it still admits the client and whether the parsers still read its
pages.
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
