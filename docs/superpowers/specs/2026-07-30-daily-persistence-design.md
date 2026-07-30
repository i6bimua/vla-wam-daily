# Daily Persistence and arXiv Capacity Fix

## Context

The first one-day quality dry-run completed successfully:

- 410 arXiv papers fetched
- 18 candidates passed the keyword prefilter
- 18 DeepSeek calls completed with no failures
- 16 papers met the publication threshold
- 14 papers received Figure metadata

The equivalent persisted run generated valid paper, analysis-cache, and
figure-cache data, then stopped before committing. A repository test required
`data/cache/figures.json` to remain an empty seed object. That requirement
contradicts the daily pipeline, whose figure cache is intentionally persisted
for reuse.

A separate three-day dry-run stopped earlier because `cs.AI` exceeded the
configured limit of 500 results per category. The schema and pagination
implementation already support up to 2000.

## Selected Design

### Production and fixture cache contracts

Keep the browser fixture deterministic: `tests/fixtures/data/cache/figures.json`
must remain the exact empty seed object.

Treat `data/cache/figures.json` as mutable generated data. Its repository test
must require valid JSON and successful typed loading, but must not require the
object to be empty. The daily workflow continues to run the complete test suite
after generation, followed by its explicit loaders for `latest.json`, archives,
analysis cache, and figure cache.

This preserves strict validation while allowing the production cache to contain
the remote arXiv Figure URLs, captions, statuses, and timestamps that the
pipeline is designed to reuse.

### arXiv capacity

Set `arxiv.max_results_per_category` to 2000 in both the schema default and the
checked-in topic configuration. Keep the three-day lookback window so a
temporary failed run does not immediately create a coverage gap.

The existing truncation guard remains active. If any category still contains
more than 2000 results within the window, the run fails visibly instead of
silently omitting papers. The existing `analysis.max_candidates: 60` limit
continues to cap DeepSeek work after keyword filtering.

## Alternatives Considered

- Use a one-day scheduled window. This passed the current live fetch, but gives
  less recovery time after an outage or delayed arXiv publication.
- Delete the figure cache before testing or committing. This would discard
  useful request results and repeat arXiv HTML work every day.
- Skip storage tests in the daily workflow. This would hide malformed generated
  data rather than correcting the contradictory assertion.
- Implement date-window query splitting. This is more complex than needed
  because the current paginator and validated schema already support 2000
  results per category.

## Test Strategy

1. Reproduce the cache-contract failure with a valid non-empty generated figure
   cache.
2. Separate the immutable fixture-seed assertion from the mutable production
   cache validation.
3. Add configuration assertions that require a 2000-result category capacity.
4. Run focused storage/configuration tests, then the complete Python and web
   suites.
5. Push and require CI/Pages success.
6. Run the already-proven one-day quality workflow in persisted mode.
7. Verify the generated data commit, daily workflow deployment, live paper
   cards, paper detail pages, and remote Figure links.

## Scope and Safety

No image bytes or PDFs are committed. Figure cache entries remain metadata and
remote arXiv URLs only. The fix does not change the DeepSeek prompt, model
profile, relevance threshold, publication schema, GitHub permissions, or
Secret handling.
