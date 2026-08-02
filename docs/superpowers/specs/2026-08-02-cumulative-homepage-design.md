# Cumulative Homepage Design

## Context

The daily pipeline intentionally fetches papers updated during a three-day
lookback window. Each successful run replaces `data/latest.json` with that
run's qualifying papers, while monthly archive files merge new records with
all previously published records. The homepage currently reads
`data/latest.json`, so its visible paper count shrinks as papers leave the
lookback window even though the archive still retains them.

## Goal

Make the homepage display every currently archived paper, newest first, so the
visible collection does not shrink when the three-day ingestion window moves.
Keep the three-day lookback unchanged for reliable ingestion and keep
`data/latest.json` as the source of the most recent run timestamp and run
statistics.

## Design

- The homepage loads run metadata from `loadLatestDataFile()`.
- The homepage loads its paper collection from `loadArchive()`, which already
  selects the current version of each arXiv ID and sorts papers by publication
  time, relevance, ID, and version.
- Homepage counts, topics, cards, filters, and search operate on the cumulative
  archive collection.
- Homepage wording changes from "Today’s selection / 今日研究" to
  "All research / 全部研究" so the interface accurately describes the data.
- The empty state refers to an empty archive rather than a day with no matching
  papers.
- The daily pipeline, lookback period, analysis cache, Figure cache, monthly
  archives, RSS, weekly page, and topic pages are unchanged.

## Failure Behavior

The existing archive loader remains authoritative. Missing, malformed, or
schema-invalid archive data continues to fail the static build instead of
silently falling back to the smaller rolling snapshot.

## Testing

- Add a homepage source contract that requires `loadArchive()` and rejects
  deriving homepage papers from `latest.papers`.
- Build the site with repository data and verify the homepage paper count
  matches the cumulative archive count.
- Run the complete frontend test suite, formatting check, Astro check/build,
  static build verifiers, and browser tests.

## Success Criteria

- The current homepage displays all 95 archived papers rather than the 36-paper
  rolling snapshot.
- Future daily runs may update or add papers but do not remove older papers
  from the homepage merely because they left the three-day fetch window.
- The archive remains the single cumulative source of published papers.
