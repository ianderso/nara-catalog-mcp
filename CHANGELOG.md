# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). The tool surface is the public
interface: renaming or removing a tool or a parameter is a major release, and
adding one is a minor release.

## [Unreleased]

## [1.1.0] — 2026-10-05

### Added

- Three tools over NARA's **Access to Archival Databases** (AAD), which the
  Catalog API cannot reach: the NUMIDENT (Social Security applications,
  claims and deaths, 1936–2007), the WWII Army enlistment cards, the
  1820–1912 ship passenger lists, and the other name-searchable series.
  `aad_list_series` lists a category's series with their files,
  `aad_search` searches one file by free text and by named fields, and
  `aad_get_record` reads one row in full with every code's meaning and a
  citation in NARA's recommended form, dated when the row was retrieved.
  The tools need no key and spend no Catalog calls. Their client reads
  only `aad.archives.gov`, one request at a time and two seconds apart,
  and caches answers for seven days.
- Every AAD answer says what a row is: an agency clerk's transcription or
  index entry, not the record. It carries AAD's own notice for the file, and
  a NUMIDENT answer adds what the NUMIDENT leaves out.
- Search summaries tell apart series that share a title. A series, as a hit
  or in a hit's `hierarchy`, now carries `creator`, the agency that made it,
  and `predecessors` when it also holds an earlier office's records. 164
  series are titled "Homestead Final Certificates", one per land office; before
  this, telling them apart took a `get_record` per hit. Every hierarchy
  level also carries its `naid`, ready for `browse_children` or
  `ancestor_naid`. A summary grows by about 150 characters a hit.

### Changed

- `NARA_API_KEY` is required only by the Catalog tools.

## [1.0.2] — 2026-09-28

### Fixed

- A tool now refuses a parameter it does not define, and names the ones it
  takes. Before, the parameter was silently dropped and the call answered as
  if it had never been given: `start_date` on `search_records`, which only
  `search_records_advanced` takes, returned every date and read as a
  filtered answer. The published schemas now say `additionalProperties:
  false`, so a client that validates can refuse before sending.

## [1.0.1] — 2026-09-28

No change to the tools. This release brings the documentation and package
metadata written since 1.0.0 to PyPI, whose page for 1.0.0 still gave the
`git+` install.

### Added

- Listing in the [MCP Registry](https://registry.modelcontextprotocol.io) as
  `io.github.ianderso/nara-catalog-mcp`: a `server.json`, and the README
  marker the registry checks to confirm the PyPI package is this project's.
  The release workflow publishes both there after PyPI.

### Changed

- The README and the package description say what the server is for:
  genealogical research. Installing is `uvx nara-catalog-mcp`.

## [1.0.0] — 2026-09-28

The first public release.

### Tools

- Finding a record: `search_records`, `search_records_advanced` (date range,
  record group, collection, ancestor NAID, level of description, microfilm
  publication, local identifier, any control number, Congress number,
  reference unit, creator, person or organisation, place, type of materials,
  recurring date, digitised-only, contribution-exists filters, exact matching
  of identifiers, `search_after` cursor paging past 10,000 hits, and each
  hit's OCR text folded in on request), `browse_children`.
- Reading a record: `get_record` and `get_record_images` (every page image
  with its object id), `download_page_image` (by page number or object id),
  `get_online_availability`, `get_partner_digital_objects`.
- Other people's readings: `get_extracted_text`, `get_transcriptions`,
  `get_tags`, `get_comments`.
- Searching the documents: `search_by_contribution_text`,
  `search_transcriptions`, `search_tags`, `search_extracted_text`,
  `search_comments`.
- Housekeeping: `api_budget`.

### Behaviour worth knowing

- Nothing writes to the Catalog. `download_page_image` writes a new local file
  and never overwrites one; every other tool is read-only, and each tool
  declares MCP annotations saying which.
- Every successful response is cached on disk, keyed by endpoint and query, to
  protect the key's monthly quota. The cache does not expire; `refresh=true`
  on any single-record reader re-reads one answer and replaces the cached
  copy.
- Live calls are counted in a ledger beside the cache, so `api_budget` reports
  the month's spend across sessions as well as this session's. A rejected
  call is counted: it reached the API.
- Every failure is returned as a structured result, never raised; API errors
  carry a plain-language `meaning` that tells a bad key (401) from a spent one
  (403).
- A `.env` file is read from the working directory only.

[Unreleased]: https://github.com/ianderso/nara-catalog-mcp/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/ianderso/nara-catalog-mcp/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/ianderso/nara-catalog-mcp/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/ianderso/nara-catalog-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ianderso/nara-catalog-mcp/releases/tag/v1.0.0
