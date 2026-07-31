# Multi-Figure Recovery Design

## Context

Paper `2607.28590v1` contains a real Figure 1 on PDF page 2 and Figure 2 on
PDF page 4. Its arXiv HTML contains both captions, but LaTeXML leaves each
`\begin{overpic}...\end{overpic}` block as text instead of emitting an
`<img>`. The current HTML parser therefore returns no usable panels.

The source archive contains the corresponding single-page PDF assets:

- `figures/introduction_v9.pdf`
- `figures/overview_v11.pdf`

The current source extractor only accepts one `\includegraphics` command in
the first figure environment, so it rejects both `overpic` figures. The PDF
extractor also rejects these pages before caption detection because PDFium's
internal character count is larger than the decoded text length. Finally,
the recovery service only installs Figure 1 and permanently skips a
`not_found` result at the current recovery version.

## Goals

- Recover Figure 1 and Figure 2 independently from arXiv HTML, source, or PDF.
- Safely recover literal local assets used by `overpic` without executing
  TeX or interpreting arbitrary macros.
- Detect PDF captions even when PDFium character and decoded-text counts
  differ.
- Retry negative recovery results after a bounded interval.
- Invalidate current negative recovery records once so historical papers are
  reconsidered.
- Preserve any existing correct panel and fill only missing Figure numbers.
- Backfill historical records and verify `2607.28590v1` on the public site.

## Non-Goals

- Execute LaTeX, shell escapes, Lua, TikZ, or arbitrary author macros.
- Guess images from filenames without a structural relationship to a figure.
- Publish a full-page screenshot as a Figure.
- Recover Figure 3 or later figures in this change.
- Replace a usable HTML or locally cached Figure with a lower-priority source.

## Chosen Architecture

### Multi-Figure recovery contract

`RecoveredFigure` gains a `number` field restricted to 1 or 2. A recovered
extractor returns a tuple of zero to two uniquely numbered figures instead of
one optional Figure 1.

The recovery service tracks which of Figure 1 and Figure 2 are missing. It
refreshes HTML first, then asks the source extractor, then the PDF extractor.
At every stage it accepts only missing numbers and never overwrites an
existing usable figure. Source results take precedence over PDF crops because
they preserve the original figure asset.

The gallery recovery status remains a summary:

- `available` when Figure 1 exists, matching the homepage preview contract.
- `not_found` when neither missing number can be recovered and no stage had a
  transient failure.
- `fetch_failed` when at least one attempted stage failed transiently.

Figure 2 may exist without Figure 1, but that does not make the Figure 1
recovery summary `available`.

### Safe source extraction

The source parser examines the first two top-level `figure` or `figure*`
environments in document order. It assigns their normal LaTeX sequence
numbers 1 and 2 only when no counter manipulation or ambiguous figure
semantics occur before them.

Each supported figure must contain:

- exactly one caption;
- exactly one asset declaration;
- either one existing `\includegraphics{...}` command or one
  `\begin{overpic}[...]{...}` environment;
- a literal local asset path that stays inside the source archive root.

For `overpic`, the parser allows only layout commands and comments that do not
alter the underlying asset. It rejects active drawing commands such as
`\put`, unsafe control sequences, nested figure constructs, multiple assets,
remote paths, traversal, or ambiguous macro-generated paths.

Literal zero-argument text macros used by captions may be expanded only when
their preamble definition is a bounded plain-text replacement. Unsupported
caption macros cause that source candidate to be skipped rather than guessed.
Single-page PDF assets continue to be rendered to PNG under the existing
size, object, dimension, and output limits.

### PDF caption and crop extraction

PDF text lines use `pdfplumber` word geometry, which provides decoded text and
bounding boxes without assuming that decoded string length equals PDFium's
internal character count. PDFium remains responsible for page objects and
final rendering.

Caption detection recognizes Figure 1 and Figure 2 separately. A candidate is
accepted only when:

- exactly one caption for that number is found in the document;
- exactly one nearby visual cluster overlaps that caption horizontally;
- the crop is smaller than the existing page-area limit;
- all existing object, text, page, pixel, and output-byte limits pass.

Each accepted crop includes only the visual cluster and caption. Ambiguous
pages return no candidate for that number.

### Negative-cache policy

Both `not_found` and `fetch_failed` recovery results are retried after 24
hours. Successful locally cached panels remain permanent.

`FIGURE_RECOVERY_VERSION` increments from 2 to 3. Existing version-2 negative
records therefore retry immediately after deployment, while version-3
negative records use the 24-hour retry interval.

### Presentation

The existing card and detail components already support Figure 1 and Figure
2 with local cached paths. No layout redesign is required. The unavailable
message changes from implying that arXiv has no figures to saying that no
reliable panel was recovered yet.

Source and PDF recovered panels retain their current provenance labels and
download behavior.

## Data Flow

1. Load the cached gallery for a paper version.
2. Keep every usable existing Figure 1 or Figure 2 panel.
3. If a negative cache entry is still inside its 24-hour retry window, stop.
4. Refresh arXiv HTML and merge usable HTML figures.
5. Ask the source extractor for still-missing Figure numbers.
6. Ask the PDF extractor for numbers still missing after source recovery.
7. Install accepted panels under
   `web/public/figures/{arxiv_id}/v{version}/fig{number}-panel1.{ext}`.
8. Persist the merged gallery in `latest.json`, every affected monthly
   archive, and `data/cache/figures.json`.

## Testing

Tests are written before production changes and must demonstrate the original
failures:

- a source archive with literal `overpic` Figure 1 and Figure 2 is rejected
  before the fix, then recovers both after the fix;
- unsafe or ambiguous `overpic` content remains rejected;
- a PDF whose decoded text length differs from the PDFium character count can
  recover Figure 1 and Figure 2;
- ambiguous or duplicate captions remain rejected;
- recovery merges HTML, source, and PDF results without overwriting a higher
  priority figure;
- Figure 2 can be installed and downloaded;
- version-2 negative results retry immediately;
- version-3 negative results retry only after 24 hours;
- the unavailable UI copy no longer claims arXiv has no figures.

The full Python, frontend, static-build, build-verifier, and Playwright suites
must pass.

## Rollout and Acceptance

1. Merge the implementation through a reviewed PR.
2. Run `sync-figures` in the daily workflow against all published records.
3. Confirm the generated data records Figure 1 and Figure 2 for
   `2607.28590v1`.
4. Confirm both local panel files exist and are non-empty.
5. Confirm the public detail page displays and downloads both figures.
6. Confirm the homepage card displays Figure 1.
7. Confirm no open implementation PR remains after merge.
