# CI Environment Compatibility Fix

## Context

The first GitHub Actions run for `main` failed in two places even though the
same application behavior passed locally:

- Python CLI assertions searched raw Rich/Typer output for option names.
  GitHub Runner forced ANSI styling into that output, so semantically identical
  text no longer contained plain substrings such as `--profile`.
- Playwright started its preview server with `pnpm exec vite`, but Vite is only
  an indirect Astro dependency and therefore has no guaranteed executable at
  the workspace root.

The application, generated data format, and deployed Pages site are not part of
the failure.

## Selected Design

### CLI output assertions

Normalize captured CLI output with the ANSI-stripping utility in Typer's
built-in Click compatibility layer before making text assertions. Keep the
original output for the existing secret-leak and traceback checks so the tests
continue to inspect what users would receive.

This makes tests independent of terminal color policy without changing CLI
runtime behavior or GitHub Actions environment variables.

### Playwright preview server

Start the E2E preview server through the project's declared Astro executable:

`pnpm exec astro preview --host 127.0.0.1 --port <port>`

Astro reads the existing `BASE_PATH` configuration and serves the already-built
static output. Playwright keeps a unique validated port and refuses to reuse an
existing server.

## Alternatives Considered

- Set `NO_COLOR` in CI. This only masks the CLI test problem on one runner and
  remains vulnerable to `FORCE_COLOR`.
- Pass color flags through every CLI invocation. This duplicates test setup and
  still couples assertions to renderer configuration.
- Add Vite as a direct dependency. This enlarges the dependency surface solely
  to reach an implementation detail already owned by Astro.
- Resolve Astro's nested Vite binary. This relies on package-manager layout and
  is less stable than Astro's public preview command.

## Test Strategy

1. Add a CLI regression test that feeds ANSI-styled option text through the
   assertion normalization path and proves the plain option remains detectable.
2. Update the Playwright configuration test to require Astro Preview and reject
   direct Vite execution; confirm it fails against the current configuration.
3. Apply the two minimal implementation changes.
4. Run focused Python and web tests, then the complete Python, formatting,
   type-check, build-verifier, and E2E gates.
5. Push to `main` and require the GitHub Actions CI run to pass before declaring
   the repair complete.

## Scope and Risks

The change is limited to test output normalization and the E2E preview command.
It does not modify production page rendering, the arXiv/DeepSeek pipeline,
figure URLs, schedules, repository permissions, or secret handling.

The main residual risk is a difference in Astro Preview CLI behavior across
platforms. The focused configuration test, real local E2E run, and hosted
GitHub Actions run cover that boundary.
