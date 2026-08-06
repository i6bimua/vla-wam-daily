# Secret Scan Boundary Design

## Problem

The repository secret scan uses `sk-[A-Za-z0-9_-]{12,}` without a token
boundary. It therefore matches `sk-state-aligned` inside the public arXiv phrase
`task-state-aligned` and blocks otherwise valid generated data.

## Design

Require `sk-` to begin at the start of a byte string or after a non-word byte:

```text
(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{12,}
```

This preserves detection for standalone keys and keys after JSON quotes,
whitespace, punctuation, or assignment delimiters. It does not match `sk-`
embedded inside an ordinary word. The existing Bearer-token pattern remains
unchanged.

## Verification and Release

Centralize the patterns in the test module and add focused cases proving that a
standalone `sk-` key and a Bearer token are rejected while
`task-state-aligned` is accepted. Run the focused test and full Python checks,
merge through CI, then manually dispatch the daily update from the repaired
`main` branch and verify its data commit and Pages deployment.
