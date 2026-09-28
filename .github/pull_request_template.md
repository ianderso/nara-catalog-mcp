<!-- What this changes and why. Link the issue it closes, if there is one. -->

## Checklist

What each item means is in [CONTRIBUTING.md](https://github.com/ianderso/nara-catalog-mcp/blob/main/CONTRIBUTING.md#what-a-change-carries).

- [ ] A test that fails without this change.
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass.
- [ ] Tool descriptions are written for the model and fit under `DESCRIPTION_BUDGET`. Raising it takes a pull request of its own, saying why.
- [ ] Any tool returning OCR, transcriptions, tags or comments says it is not evidence.
- [ ] Failures come back as an `error` result, not an exception.
- [ ] If a tool or parameter changed: `tests/fixtures/tool_schema.json` is regenerated and the README tables are updated.
- [ ] If this relies on how the live Catalog answers: what was observed, and when, is in `docs/API-NOTES.md`, and captured payloads carry no personal names.
- [ ] A `CHANGELOG.md` entry, if users will notice the change.
- [ ] Nothing here writes to the Catalog.
