# Permanent Figure Archive and Home Preview Design

Date: 2026-07-30
Status: Approved in conversation; awaiting written-spec review

## Goal

Keep the three-day arXiv lookback as a catch-up window while permanently
retaining every published paper, its DeepSeek analysis, and locally cached
Figure 1 / Figure 2 files. Show Figure 1 directly on each home-page paper card
and reduce the title size so the paper list is visually scannable.

This design applies only to papers that meet the configured publication
threshold. The hundreds of raw arXiv records rejected by the keyword and
relevance filters are not archived.

## Current Behavior

- `data/archive/YYYY-MM.json` permanently retains published paper records.
- `data/cache/analyses.json` retains successful DeepSeek analyses.
- `data/cache/figures.json` retains Figure status, captions, and remote arXiv
  URLs.
- Figure image bytes are not currently stored by the repository.
- Figure 1 and Figure 2 appear only after opening a paper card's details.
- The home-page title can grow to `3rem`, leaving no immediate
  visual preview.

The scheduled three-day lookback affects retrieval only. It is not a retention
period and must not be used to delete archives or caches.

## Selected Approach

Mirror Figure 1 and Figure 2 for published papers into the repository and serve
them as GitHub Pages static assets.

Files use deterministic, version-aware paths:

```text
web/public/figures/{arxiv_id}/v{version}/fig{number}-panel{index}.{extension}
```

The existing remote arXiv URLs remain the canonical source references and the
runtime fallback. A local asset path is recorded only after the corresponding
file has been completely downloaded, validated, and atomically installed.

This approach is selected because it:

- requires no server or additional storage provider;
- makes historical Figure previews independent of future arXiv image-path
  changes or transient availability;
- integrates directly with the existing GitHub Pages deployment;
- keeps repository growth bounded by mirroring only published papers.

## Alternatives Considered

### Keep remote URLs only

This has the smallest repository footprint and matches the current
implementation, but it does not permanently preserve the image files and can
leave old pages without images when remote URLs change.

### Use external object storage

Object storage scales better for a very large archive, but it adds credentials,
billing, lifecycle management, and another service dependency. It is not
needed at the current publication volume. The deterministic path contract
allows migration later without changing the user-facing layout.

## Figure Asset Contract

Each `FigureAsset` continues to store:

- Figure number and label;
- plain-text caption;
- canonical arXiv image URLs;
- canonical arXiv HTML source anchor.

It additionally stores one local path for each successfully mirrored panel.
Panel order must correspond to the canonical URL order. Missing local panels
remain explicit and use the canonical URL as their display and download
fallback.

Local paths must:

- be relative web paths under `/figures/`;
- contain the matching arXiv ID and version;
- contain no parent-directory traversal, query string, fragment, credentials,
  or external host;
- use a supported raster or SVG extension derived from validated response
  content rather than blindly trusting the URL suffix.

Existing archive records without local paths remain valid and are eligible for
backfill.

## Download and Persistence Flow

For each paper that meets the publication threshold:

1. Reuse the versioned Figure metadata cache when it is fresh.
2. For Figure 1 and Figure 2, check whether each deterministic local panel
   already exists and is valid.
3. Download only missing panels from the already allowlisted arXiv URLs.
4. Enforce HTTPS, the arXiv host allowlist, safe redirects, a response-size
   limit, and accepted image content types.
5. Stream to a temporary file, validate that the response is non-empty, then
   atomically move it into place.
6. Record successful local paths in the Figure cache and published record.
7. Persist the updated monthly archive, latest file, analysis cache, Figure
   cache, and generated Figure files in the same daily update.

A Figure download failure is non-fatal. The paper still publishes with its
remote arXiv URL, and a later daily run retries the missing local asset. Files
are never deleted merely because they fall outside the three-day retrieval
window.

The daily workflow may stage only:

```text
data/**
web/public/figures/**
```

It must reject any unexpected staged path before committing generated data.

## Historical Backfill

A dedicated, idempotent Figure synchronization command scans the permanent
monthly archives, downloads missing Figure 1 / Figure 2 panels, and updates the
affected archive records and Figure cache.

The initial deployment runs this synchronization once so all currently
published papers receive local assets where arXiv provides them. Future daily
runs perform the same operation for newly published papers and retry prior
failures without re-downloading valid files.

## Home-Page Presentation

Every non-compact paper card shows a Figure 1 preview without requiring the
reader to open the details panel.

Desktop layout:

- metadata, smaller title, authors, and one-sentence summary form the text
  column;
- the first Figure 1 panel forms the visual column;
- the preview links to the paper detail page, where all Figure 1 / Figure 2
  panels, captions, and download actions remain available.

Mobile layout:

- title and metadata appear first;
- Figure 1 appears immediately below them at full card width;
- the structured notes and full Figure gallery remain in the expandable area.

The Chinese title uses `clamp(1.35rem, 2.4vw, 2rem)` instead of the current
`clamp(1.75rem, 3.6vw, 3rem)`. Long titles wrap normally and must not be
truncated.

Preview behavior:

- prefer the local cached path;
- fall back to the canonical arXiv URL if no local file is recorded;
- use the Figure caption for accessible alternative text;
- use lazy loading and reserve aspect-ratio space to reduce layout shift;
- show a neutral “Fig. 1 暂不可用” panel when Figure 1 does not exist or both
  local and remote loading fail.

Compact cards may omit the preview to preserve their intentionally dense
layout.

## Detail Page and Downloads

The existing full Figure gallery remains the source of Figure 1 / Figure 2
captions and panel-level controls.

- Display and download actions prefer locally cached files.
- “查看 arXiv 原图” remains available as a separate source link.
- If a local file is missing, the existing remote download/open fallback
  remains active.
- Source and copyright notices remain visible. Mirroring a file does not change
  its ownership or the paper's reuse terms.

## Dependabot Pull-Request Policy

The nine current failed Dependabot pull requests will be closed after the
replacement policy is committed.

Future updates are grouped by ecosystem:

- Python / uv;
- frontend / npm;
- GitHub Actions.

Each ecosystem may have at most one open Dependabot pull request. Routine
compatible updates are tested and handled without asking the user. Automatic
major-version pull requests for Python and frontend dependencies are ignored;
major upgrades are reviewed intentionally because they can require code or CI
migrations. Human-authored pull requests are never automatically closed by
this policy.

## Error Handling and Safety

- Invalid or non-arXiv remote URLs are rejected before network access.
- Redirects may not change the paper identity or leave the arXiv host
  allowlist.
- Image responses have strict type and size limits.
- Partial files are temporary and never exposed by Pages.
- Existing valid local files are not overwritten unless the versioned source
  identity changes.
- A single image failure cannot block paper publication or deployment.
- Generated-file staging is restricted to the two documented directories.
- The repository keeps canonical arXiv links, captions, author attribution, and
  a reuse notice on every Figure presentation.

## Testing

Python tests cover:

- deterministic safe local paths;
- accepted and rejected image types, redirects, empty bodies, and size limits;
- atomic installation and cleanup of partial files;
- cache hits and retryable failures;
- preservation of historical archives beyond the three-day lookback;
- idempotent archive backfill;
- workflow staging restrictions.

Astro and browser tests cover:

- Figure 1 is present before expanding a home-page card;
- the title uses the reduced responsive size;
- local assets are preferred over remote URLs;
- remote and unavailable-state fallbacks;
- mobile stacking and accessible alternative text;
- detail-page Figure 1 / Figure 2 display and downloads remain functional.

Full Python, TypeScript, formatting, build, and Playwright suites must pass
before deployment. The deployed site is then checked on desktop and mobile
against at least one paper with a local Figure 1 and one paper without one.

## Acceptance Criteria

- The three-day lookback remains the scheduled retrieval window.
- All published paper records and DeepSeek analyses remain permanently
  archived.
- Every available Figure 1 / Figure 2 panel for published papers is mirrored
  locally, including a one-time historical backfill.
- Missing or failed local assets reliably fall back to arXiv and never block a
  daily run.
- Home-page cards show Figure 1 immediately and use the smaller title scale.
- Detail pages retain complete Figure 1 / Figure 2 captions and download
  controls.
- Dependabot no longer opens a separate flood of routine dependency pull
  requests.
