# Design

Why the server is shaped the way it is. What each tool does is in the
README; what the Catalog API actually does, as opposed to what its spec
says, is in [API-NOTES.md](API-NOTES.md). This is the layer between: the
decisions, and the reasons they were made.

## The stance: a description is a finding aid

The Catalog describes what NARA holds. A search hit is a lead; the page
images are the evidence; and every other reading of a page (NARA's OCR, a
partner's OCR, a volunteer's transcription, a citizen's tag, a researcher's
comment) is a lead too, pointing at the page worth opening. The server
exists to find the image worth reading, not to replace reading it.

That stance is enforced, not just stated. The description of every tool
that returns somebody else's reading of a document must say "not evidence"
and point back at the image, and a contract test fails if one stops doing
so. The model acts on the description, so that is where the distinction
has to live.

Those readings are also untrusted text. Tags, comments and transcriptions
are written by members of the public and reach the model verbatim, so one
could carry instructions aimed at it. The server's instructions tell the
model to treat that text as material to weigh, never as instructions to
follow; the model is still the one deciding, which is why the one tool with
a local side effect is built so that a bad instruction cannot do harm
(below).

## The surface, and why each part is there

Eighteen tools over the Catalog's 59 paths. The ones with research
value are covered; the rest are out by decision (below).

**The evidence path.** `get_extracted_text` (`/extractedText/{naId}`),
`get_transcriptions`, `get_tags` and `get_comments` (`/{kind}/naId/{naId}`),
and `download_page_image`, which fetches the `objectUrl` itself. The media
host is open and needs no key, so none is sent and no quota is spent; the
one Catalog call resolves the page list, and that is cached. Open is not
unrestricted: most holdings are public domain, but some descriptions record
use restrictions, so the README says to check before republishing.
Every reading names its `object_id`, `get_record_images` lists the object id
beside each page, and `download_page_image` takes an object id as well as a
page number, so "this page mentions the widow" becomes "download this page"
without a further read of the record.

**Two search tools, not one.** `search_records` stays at title, query, limit
and page; `search_records_advanced` carries the filters that narrow a common
name. The split keeps the everyday tool small: every parameter is read by
the model on every call, and one tool with thirty parameters would tax every
search to serve the rare one.

**Searching the documents rather than the catalogue.** The four `by-*`
routes return records whose transcriptions, tags, OCR or comments match, and
`search_by_contribution_text` filters the main index by the same text and
returns full record summaries. A pension file titled only with the veteran's
name will name his widow, his children and his witnesses in its transcribed
text, none of which a title search reaches.

**The hierarchy.** `browse_children` walks down (`/records/parentNaId/`);
`get_record` returns the ancestors, so a caller can read up. A citation is
only locatable if you know where the record sits.

**Where else the record lives.** `get_online_availability` and
`get_partner_digital_objects` resolve a hint from a subscription site back
to the archival original. Each handles the empty answer the live Catalog
gives (a prose message, and a 404) rather than the empty list the spec
implies.

## Decisions inside the search tools

**Refinements are refused locally.** Some parameters narrow a search but
cannot be one; the Catalog answers HTTP 400 "No search terms entered" to
them alone, and nothing in the spec says which. The tool classifies them
(the set is in `server.py`, the evidence in API-NOTES) and answers
`refinements_only` without spending the call.

**The cursor rule lives in the parameter's own description.** Paging past
10,000 hits needs `searchAfter`, and it only works if the first request
already carried `searchAfter=*`; a cursor from a relevance-sorted search
returns zero hits. The tool reports the boundary when a page search passes
it, and the parameter says what to do.

**Exact forms are a flag, not three more parameters.** `title`,
`local_identifier` and `microform_publication` each have an `_is` twin that
matches the whole identifier rather than words within it. One `exact` flag
switches all three, which costs one schema entry instead of three and
cannot be half-applied.

**Folded text is capped and cautioned.** `include_extracted_text` asks for
NARA's OCR and the partners' text together and folds one reading per page
into each hit, capped at 2,000 characters, with the same caution the
dedicated tool carries. Whole pages come from `get_extracted_text`, which
also reports the object total and the next results page, so a sixty-page
file is not mistaken for a twenty-page one.

**What is deliberately not sent.** 15 of the 55 search parameters, each
with its reason in API-NOTES: response shaping the server does itself,
internal control numbers, a parameter the spec says is identical to
another, two templates over arbitrary field names, and a change feed.

## The call budget

A key is capped at 10,000 calls a month, and a surname sweep spends them
faster than it feels. Three things follow.

**Every response is cached, forever.** Keyed by path and every query
parameter, so two routes taking the same parameters never share an entry
and a filter added later cannot alias an older one; a test derived from
the tool's own schema holds this. A failed response is never cached, and a
corrupt entry is re-fetched rather than raised. Nothing expires, because a
description rarely changes and a spent call cannot be recovered.

**`refresh` is the way to see a change.** Every tool that reads one record
takes `refresh=true`, which re-reads that one answer and replaces the cached
copy. Contributions accumulate and paper gets scanned, so a cached "nothing
here" is the answer most worth refreshing; deleting the cache directory to
get at it would throw away everything else, and the month's ledger with it.
A contract test holds the flag to exactly the tools whose answer can go
stale.

**The ledger sees the month.** Live calls are counted in memory for the
session and in `budget.json` beside the cache for the UTC month, read,
incremented and replaced atomically on every request. A request that fails
is counted: it reached the Catalog and spent quota. `api_budget` reports
both figures and says what the ledger cannot see: another machine, another
cache directory, or a second server writing at the same instant, which can
lose one count. That undercount is accepted for a personal key; the
Catalog's own count is the authority.

## The one tool that touches the machine

`download_page_image` creates a local file, and the path comes from the
model, which has just read text written by strangers. So the tool only ever
creates a new file. An existing destination is refused before any call is
spent; the write uses exclusive creation, so a file that appears between the
check and the write is refused too; and the cleanup after a failed transfer
removes only a file this call created. The body is streamed rather than
buffered, because a digital object is not always a page scan, and the format
is named from the bytes rather than the URL, because an HTML error page with
a 200 would otherwise pass for a download. Every tool carries MCP
annotations: seventeen are marked read-only, and this one is marked as
writing but not destructive, so a client can ask before it runs.

## Configuration

A `.env` file is read from the working directory only. python-dotenv's
default searches upward from the calling module, which for an installed
package is `site-packages`: it would ignore the `.env` beside the user and
could read an unrelated one from a parent directory. A missing or
placeholder key, or a numeric setting that is not a positive number, is
reported on the first tool call as `not_configured`, naming the variable.

## Out of scope, by decision

The API can post tags and comments and put transcriptions. Contributing a
transcription back is a real public good, but it publishes under the
operator's key and nothing here can withdraw it. If it is ever added it
should be a separate, explicitly enabled tool, never part of the default
surface. A contract test asserts that nothing in the published schema
invites a write.

Also skipped: `/announcements`, `/users`, `/partners`, `/justifications`,
`/records/stats`, `/fetch-coords`. Catalog site plumbing, not research.

## The published schema is a cost

Every tool description and parameter ships to the model on every session
before any work happens. So descriptions are written for the model rather
than the developer; a contract test caps the combined description block,
and the cap is raised deliberately, never by accident; and pydantic's
auto-generated schema titles, roughly a tenth of the block and carrying
nothing, are stripped at import. The stripping walks the schema's structure
rather than every dict it meets, because a parameter named `title` (the
record search has one) must not be mistaken for the keyword.

## How the suite is built

Every test runs mocked, with no key. The fixtures are a small corpus rather
than one specimen: a digitised item, an undigitised description, a series
with children, contributions of each kind, the live `/extractedText`
envelope, and a record with nulls where lists belong and junk inside them,
because Catalog records are uneven and the shaping code is where that bites.

Contract tests assert the surface as a client sees it: every tool and
parameter described, a name-and-parameter snapshot (regenerated
deliberately with `uv run python -m tests.regen_tool_snapshot`), the
description ceiling, the README cross-checked three ways, and a sweep that
calls every tool against a failing API and requires a structured error
back. The sweep's argument builder is itself tested, because it once
stopped reaching tool bodies: every tool returned a tidy validation error,
and the sweep kept passing while testing nothing.

What the mocks cannot answer, `tests/live_check.py` asks the Catalog with a
real key: it re-verifies where the two text flags put their text and
re-runs the parameter audit against the live spec, so a change on NARA's
side shows up as a diff in its output. Its pure helpers are unit-tested;
the script itself is not collected.

CI runs the suite on Linux at every supported Python, on macOS and Windows
at one, and once more at the lowest declared version of every dependency,
so the floors in `pyproject.toml` stay honest; it also builds the
distributions and checks them.

## Known gaps

Not decisions against, just not done.

- **Use restrictions are not surfaced.** The Catalog records them on
  individual descriptions, and `get_record` does not return them, so a
  caller cannot tell from this server whether an image may be republished.
  The field names need verifying against live records before they are
  shaped.
- **Downloads are not confined.** `download_page_image` never overwrites,
  but it will create a file anywhere the server's user can write. A
  `NARA_DOWNLOAD_DIR` that confines it to one directory would suit anyone who
  wants a tighter boundary than a per-call approval.
- **Not on PyPI**, so `uvx nara-catalog-mcp` needs the `--from git+…`.
- **Python 3.14** is not declared until the suite has run on a final release.
