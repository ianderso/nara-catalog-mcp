# API notes

Corrections to `https://catalog.archives.gov/api/v2/swagger.json`, verified
against the live Catalog on 2026-09-23. The spec is unreliable in specific,
repeatable ways; each of these cost a wrong implementation once.

## `required` flags do not mean what they say

| Parameter | Spec says | Actually |
| --- | --- | --- |
| `objectId` on `/extractedText/{naId}` | `required: true` (query) | Optional. Omitting it returns every digital object for the NAID, paginated. |
| `naId` path parameter (`paramPathNaId`) | `required: false` | Required — it is a path segment, so it cannot be optional. |
| `q` on `/records/search` (`paramQuery`) | `required: true` | Optional. A `title`-only search works, and is the more precise one. |

Two further definitions of `q` (`paramContQuery`, `paramOETQuery`) mark it
optional, so the spec contradicts itself on the same parameter name.

## `maximum` is used where `maxLength` is meant

`q` carries `{"maximum": 1024, "type": "string"}` and the `naId` query
parameter `{"maximum": 10000, "type": "string"}`. In JSON Schema `maximum`
bounds a *numeric value*, not a string's length. Read these as length limits.

The path `naId` is worse: `{"maximum": 30, "type": "integer"}`. Taken
literally that rejects every NAID above 30. It means a 30-character limit.

## `searchAfter` is defined twice, and neither type is right

- `paramSearchAfterRecord`: `{"maximum": 100, "type": "integer"}`
- `paramSearchAfterContribution`: `{"maximum": 100, "type": "string"}`

Neither holds. The cursor is the `sort` array of the last hit, serialized as
comma-joined values, and its shape depends on how the search was started:

- An ordinary search sorts by relevance, and its `sort` is a **mixed pair** —
  `[104.13167, "55033830"]`, a float and a string. **This cursor is not
  resumable.** Feeding it back returns HTTP 200 with zero hits.
- A search started with `searchAfter=*` sorts deterministically, and its
  `sort` is a **single-element array** — `[680603311]`, the NAID alone. That
  one resumes correctly.

So cursor paging only works if the **first** request already carries
`searchAfter=*`. The parameter's own description says this; the schema does
not. `searchAfter` is also incompatible with `sort` and `page`.

This server sends `*` only when the caller asks for it, and
`search_records_advanced` documents the requirement in the parameter itself.

## Response shapes the spec describes only in prose

- **`/extractedText/{naId}`** returns a paginated object carrying
  `digitalObjects`, not a bare array. Each entry nests its text at
  `otherExtractedText.contribution`, with the contributor alongside — often a
  partner such as FamilySearch rather than NARA.
- **`/transcriptions/naId/{naId}`** reuses the Elasticsearch envelope
  (`body.hits.hits`), the same as the search routes.
- **`/online-availability/naId/{naId}`** answers with
  `{"response": "No Online Availability status available for naId ..."}` when
  nothing is recorded, rather than an empty list.
- **`/metadata/naid-availability/naid/{naId}`** returns `{"ids": [...]}`, and
  `404` where no partner metadata is matched. An empty list and a 404 both
  mean "nothing matched", so the tool reports them identically.

## `/extractedText/{naId}` paginates, and says so at the top level

Beside `digitalObjects` the response carries `page`, `limit` and `total`.
`total` counts digital objects across every page of results, not characters,
and it is an integer while `naId` in the same object is a string. A 58-page
file at the default `limit=20` is three calls; `get_extracted_text` reports
`total_objects` and `next_results_page` so the caller is not left believing
that twenty pages were the file. Captured live 2026-09-23.

## The two text flags on `/records/search`, and where each puts its text

With both flags set, each hit's `digitalObjects[]` entries gain exactly two
things:

- `extractedText`: a flat string, NARA's own OCR (`includeExtractedText`).
- `otherExtractedText`: a **list** of contribution objects, each with a
  `contribution` string (`includeOtherExtractedText`). The `/extractedText`
  route returns the same partner text as one object, not a list.

On NAID 54765873, 56 of the 58 objects carried both. `extracted_page` reads
either spelling, prefers partner text when both are present to match what
the dedicated route returns, and takes the first list entry that carries
text. Verified live 2026-09-28 with `tests/live_check.py`, which re-checks
both paths on every run.

## Parameter count

`/records/search` resolves to **55** parameters once `$ref`s are followed.
The tools send 40 of them: the table below, plus `naId` (`get_record`), the
four `*Contribution` filters (`search_by_contribution_text`), `limit`,
`page` and `sourceIncludes`.

## What `search_records_advanced` sends

| Tool parameter | API name |
| --- | --- |
| `title`, `query` | `title`, `q` |
| `start_date`, `end_date`, `exact_date` | `startDate`, `endDate`, `exactDate` |
| `available_online` | `availableOnline` |
| `local_identifier`, `microform_publication` | `localIdentifier`, `microformPublicationsIdentifier` |
| `exact` | switches `title`, `localIdentifier` and `microformPublicationsIdentifier` to their `_is` forms |
| `control_numbers` | `controlNumbers` |
| `record_group_number`, `collection_identifier`, `ancestor_naid` | `recordGroupNumber`, `collectionIdentifier`, `ancestorNaId` |
| `level_of_description` | `levelOfDescription` |
| `reference_units`, `creators`, `geographic_reference`, `person_or_org` | `referenceUnits`, `creators`, `geographicReference`, `personOrOrg` |
| `transcriptions_exist`, `tags_exist`, `comments_exist`, `contributions_exist` | the same |
| `type_of_materials`, `data_source` | `typeOfMaterials`, `dataSource` |
| `recurring_month`, `recurring_day` | `recurringDateMonth`, `recurringDateDay` |
| `congress_number` | `congressNumber` |
| `include_extracted_text` | `includeExtractedText` and `includeOtherExtractedText` |
| `search_after` | `searchAfter` |

## Refinements are not search terms

Some parameters narrow a search but cannot be one. Passing only these returns
HTTP 400 with `"No search terms entered"`:

`dataSource`, `availableOnline`, `digitalObjectCount`, `transcriptions_exist`,
`tags_exist`, `comments_exist`, `contributions_exist`, `abbreviated`, `debug`,
`controlGroup`, `includeOtherExtractedText`, and the two templates
`[fieldName]_exists` and `[fieldName]_is_not`

These *do* work standalone: `typeOfMaterials`, `levelOfDescription`,
`objectType`, `recurringDateMonth` / `recurringDateDay`, `controlNumbers`,
`beginCongress` / `endCongress` / `congressNumber`, every `_is` exact-match
variant (`title_is`, `naId_is`, `localIdentifier_is`,
`microformPublicationsIdentifier_is`, `variantControlNumber_is`,
`variantControlType_is`), `variantControlType`, plus the obvious `title`,
`q`, `naId` and the date parameters.

`ingestTimeStart` and `ingestTimeEnd` answer **500**, not 400, to a value
that is not a date, so a malformed value there looks like an outage.

Nothing in the spec distinguishes the groups. Verified live 2026-09-23 and,
for the 22 parameters the server did not then send, 2026-09-28 with
`tests/live_check.py --classify`, one parameter at a time.
`search_records_advanced` classifies the ones it sends and answers
`refinements_only` locally, rather than spending a call to be told "No
search terms entered".

## Parameters deliberately not exposed

Of the 55, the tools send 40 after the 2026-09-28 audit. The other 15, and why:

| Parameter | Why not |
| --- | --- |
| `abbreviated`, `debug`, `digitalObjectCount` | Response shaping. The server shapes its own responses. |
| `naId_is` | The spec says it "currently functions exactly as `naId`". |
| `beginCongress`, `endCongress` | `congressNumber` covers the common question; the spec forbids combining them with it. |
| `controlGroup`, `variantControlNumber_is`, `variantControlType`, `variantControlType_is` | Internal control numbers. `controlNumbers` searches variant control numbers among everything else. |
| `objectType` | Filters by file format of the digital objects. Researchers pick pages, not formats. |
| `[fieldName]_exists`, `[fieldName]_is_not` | Templates over any field name, not parameters. A general exclude filter would be a design decision of its own. |
| `ingestTimeStart`, `ingestTimeEnd` | "Descriptions ingested since": a change feed, not a research question, and a 500 for a bad value. |
| `sort` | Not a `/records/search` parameter. It appears only in `searchAfter`'s description as incompatible; `paramSortBy` belongs to the contribution database routes. |

## Creators sit on the series, not on the record group or the file

Many series share a title. A search on `title_is="Homestead Final
Certificates"` with `levelOfDescription=series` finds **164** series (230 by
words), one per land office, and the title alone cannot tell them apart. What
does is the series' **creator**: an authority heading such as
"Department of the Interior. General Land Office. Sidney (Nebraska) Land
Office. 7/2/1887-2/28/1906". Observed live 2026-10-05:

- A series record carries `creators[]`, each with `heading`, `creatorType`,
  `authorityType`, `naId` and the office's establish and abolish dates. On a
  page of 100 such series, all 100 named a creator and the 100 headings
  were all different.
- A file unit or item carries **no** `creators` of its own (NAID 63668992,
  a digitised homestead file). Its series does, inside `ancestors[]`:
  each ancestor carries `naId`, `title`, `levelOfDescription`, `distance`
  and, for a series, the same `creators[]` list.
- A record group carries none, either as a hit (NAID 378, RG 49) or as an
  ancestor. On a page of 100 pension hits and 100 RG 49 land-entry file
  units, every creator found was on a series ancestor.
- `creatorType` is "Most Recent" or "Predecessor". A series that absorbed
  an earlier office's records lists both, and **not always most recent
  first**: 7820365 lists Omaha, West Point and Norfolk (predecessors)
  before Neligh (most recent). One series, 6037952, lists only a
  predecessor.
- `sourceIncludes` selects nested paths: `ancestors.creators.heading`,
  `ancestors.creators.creatorType`, `ancestors.naId`, `creators.heading` and
  `creators.creatorType` all come back, and nothing else of the creator does.

So `LEAN_FIELDS` asks for those five paths, and a summary carries `creator`
(the most recent, else the first listed) and `predecessors` wherever the
Catalog gives them: on a series hit, and on a series inside `hierarchy`.
Each hierarchy level also carries its `naid`, which `browse_children` and
`ancestor_naid` take. Measured on the pages above, the summary grows by
120 to 180 characters a hit, most of it the heading. `tests/live_check.py`
re-checks this (two calls).

## `includeOtherExtractedText` rides with `includeExtractedText`

`include_extracted_text` sends both flags, so a hit carries NARA's OCR and
the partners' text together. The list shape above is what the search route
delivers; before it was handled, partner text on a search hit was skipped,
which looked like a record with no partner text rather than a parsing gap.

## Media is open

An `objectUrl` answers 200 with **no `x-api-key` and no redirect** — the media
host is open. So:

- The key is not sent on image fetches. It is unnecessary, and sending a
  credential where it is not needed is gratuitous.
- An image download is not an API call and does not count against the 10,000
  monthly cap.

Open is not the same as unrestricted. Most of NARA's holdings are in the public
domain, but not all: some descriptions record use restrictions, such as
copyright in donated materials. Check the record before republishing an image.

## AAD (`aad.archives.gov`), observed 2026-10-05

The Access to Archival Databases is a separate NARA web application with no
API. What the `aad_*` tools rely on, as seen on this date:

**Access and terms.**

- `robots.txt` does not exist: the path answers 302 to `index.jsp`.
  `www.archives.gov/robots.txt` (another host) sets `Crawl-delay: 10` for
  all agents and disallows nothing under AAD.
- AAD's Getting Started Guide and FAQ set no terms on automated access. The
  restrictions they state are FOIA's: every file in AAD is either fully open
  or a public-use version with exempt data withheld. The site-wide privacy
  and use policy (`www.archives.gov/global-pages/privacy.html`) says
  traffic is monitored for attempts to change or damage the system, asks
  that people link rather than mirror the site, and says federal works are
  generally in the public domain. Nothing forbids a paced, identified
  reader.
- AAD's load balancer (`awselb/2.0`) answered **403** to the User-Agents
  `nara-catalog-mcp/1.0.2 (+https://github.com/ianderso/nara-catalog-mcp)`
  and `nara-catalog-mcp/1.0.2`, and **200** to
  `Mozilla/5.0 (compatible; nara-catalog-mcp/1.0.2;
  +https://github.com/ianderso/nara-catalog-mcp)`. The client sends the
  last.

**Pages and parameters.**

| Page | Parameters | What it gives |
| --- | --- | --- |
| `series-list.jsp` | `cat`, e.g. `GP21,22,23,24,44` (Genealogy/Personal History) | Each series (`series-description.jsp?s=<id>`), its record group, row count, and its files (`fielded-search.jsp?dt=<file id>`) with row counts. |
| `fielded-search.jsp` | `dt`, `tf=F` | The file's search fields: per field a column id `c_id`, a hidden `nfo_<c_id>` (`V,20,1900` text, `N,4,1900` number) and either `txt_<c_id>` with `op_<c_id>` or, for a coded field, `cl_<c_id>`; `sc`, the default display columns. |
| `display-partial-records.jsp` | `dt`, `sc`, `q`, per field `nfo_`/`op_`/`txt_`, `rpp` (10, 20 or 50), `pg` | "You found N partial records out of M total records in this file", "Page p of n", and a `queryResults` table whose rows link `record-detail.jsp?…&rid=<id>`. |
| `record-detail.jsp` | `dt`, `rid` | Every field: title, value, and the code's meaning. |

- Operators: text `0` all of the values, `1` any, `2` exact phrase;
  number `3` equals, `4`–`7` less/greater than (or equal), `8` between,
  which takes `txt_<c_id>` twice. A range search on NUMIDENT birth years
  (`1930-1940`) returned the expected rows.
- Free text (`q`) matches code meanings: `BEILIN RUSSIA` found 27 rows
  whose country is stored as code 44. When a free-text match falls in a
  column not displayed, the row is followed by a second row naming the
  field that matched (`rowspan="2"` on the first cell).
- Every search answers first with a 711-byte page titled "Please Wait..."
  carrying `<meta http-equiv='Refresh' content='0'>`. Requesting the same
  URL again, with the session's cookies (`JSESSIONID`, `AWSALB`), returns
  the results.
- Zero matches is an ordinary results page: "You found 0 partial records".
- An unknown file id answers **404** with "the page you are trying to
  access is not available". An unknown record id answers **200** with
  every field blank.
- Every search and record page carries the same header: the file unit, the
  series with its dates, the record group (or collection) with its title in
  a tooltip, and a series `(info)` link. A `<p id="disclaimer">` carries the
  file's notice. For the NUMIDENT it says the files do not hold every SS-5
  and that bookmarks from before 30 September 2026 may no longer work,
  because NARA added records and redacted more data on potentially living
  people: **record ids are not stable across reloads.**
- The CSV download (`popup-download.jsp`, up to 1,000 rows) exists and is
  not used: a results page and a record page are smaller, and enough.

**The NUMIDENT** (series 5057, 24 files: application, death and claim, each
split by surname range). From the series description and NARA's FAQ
(`content/aad_docs/rg047_num_faq_2026Sep.pdf`):

- It holds only people with a verified death or who would have been over
  110 by 31 December 2007; SS-5 rows exist only for verified deceased
  people or those born before 1908.
- SS-5 information before 1973 may be incomplete (SSA converted legacy
  applications 1973–79). The death files omit state-reported deaths, an
  estimated 10–30% of all, and deaths before 1962 may be missing; no death
  row is not proof of life. Claim rows stop by 1984 and name the account
  holder, not the claimant.
- NARA masked 0.15% of application rows, of potentially living people, by
  filling every field with Z; they sit in the U–Z file.
- The original SS-5 is requested from SSA under FOIA.

A row is not the record: Elvis Aron Presley's SS-5 row (file 3433, record
4118111) gives his birth year as 1934. He was born in 1935.

**Citing.** The Getting Started Guide asks that a record retrieved from AAD
be cited by its file, series and record group, with the retrieval date from
AAD in brackets. `aad_get_record` builds that citation, using the date the
cached copy was fetched.

`tests/live_check.py` re-checks the client against AAD: the User-Agent is
admitted, a fielded search and a record still parse.
