# Twice-Daily arXiv Schedule Design

## Goal

Run the existing daily ingestion pipeline twice each day at 07:00 and 12:00 in
`Asia/Shanghai`. The noon run catches arXiv metadata that was not yet available
to the morning run.

## Design

Add a second schedule entry to `.github/workflows/daily.yml`. The two entries
are:

- `0 7 * * *` with `timezone: Asia/Shanghai`
- `0 12 * * *` with `timezone: Asia/Shanghai`

Both triggers execute the same `update`, `build`, and `deploy` jobs. They share
the existing analysis cache, Figure cache, cumulative monthly archives, and
three-day ingestion lookback. Existing identity-based persistence prevents a
paper from being duplicated when both runs observe it.

The existing `daily-data-update` concurrency group remains unchanged with
`cancel-in-progress: false`. If GitHub delays the morning run until it overlaps
the noon run, the later run waits instead of cancelling or overwriting the
earlier run.

## Empty Results and Failures

A zero-paper response remains a valid successful run because weekends and arXiv
publication gaps can legitimately contain no papers. The noon schedule provides
the same-day catch-up path without treating an empty morning response as an
error. Network, validation, DeepSeek, persistence, build, and deployment errors
continue to fail the workflow under the existing behavior.

## Documentation and Tests

Update the workflow structure test to require exactly the two Beijing-time
schedules. Update the README operations section to state that automated runs
occur at 07:00 and 12:00 Beijing time and that GitHub may start them late.

Verification consists of the focused workflow and documentation tests followed
by the complete Python test suite, Ruff, mypy, and YAML parsing through the
existing workflow test loader.

## Release

Publish the change through the normal pull-request and CI flow. After the change
reaches `main`, manually dispatch one non-dry-run daily update so the papers that
arrived after today's morning run are processed immediately rather than waiting
for the next scheduled trigger.
