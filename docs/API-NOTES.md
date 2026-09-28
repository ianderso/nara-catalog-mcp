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
