# Release Notes — v3.24.0

## Overview

v3.24.0 hardens the autonomous agent introduced in v3.23.0. The focus is on
making the local "brain" faster and more predictable across long multi-step
tasks, allowing a run to be interrupted while the brain is generating, and
verifying end-to-end that media tools can be chained through file-based
results.

## Highlights

- **Faster, more efficient brain**
  - The selected brain is now loaded once and reused across ReAct turns rather
    than reloading weights from disk on every step. Switching to a different
    brain still reloads as expected.
  - Long multi-step scratchpads are managed within the model's context window:
    the oldest intermediate steps are trimmed while the system guidance and
    the latest reasoning anchor are retained, so long jobs no longer overflow
    the context.
  - Brain loading and generation are serialized, and requests that exceed the
    context window are rejected with a clear error.
- **Interrupt while generating**
  - Stop now takes effect mid-generation (token-by-token) instead of only
    between steps, so a run can be cancelled immediately even while the brain
    is producing a long response.
- **Verified media-tool chaining**
  - End-to-end coverage confirms the brain can drive a media tool, see the
    persisted artifact path fed back in the next observation, continue to a
    second tool, and degrade gracefully when an engine is not installed.

## Compatibility

- Same system requirements as v3.23.x; settings, sessions and downloaded
  models carry over without migration.

## Verification

- Python test suite: 1679 passed (including new brain-hardening, mid-generation
  cancellation, and end-to-end media-chain tests).
- JavaScript syntax checks: 51 files passed; renderer unit tests and packaging
  smoke tests: passing.
- ruff: clean.
- These changes are covered by deterministic tests; on a low-power CPU the
  small CPU brains remain the recommended starting point.

## Notes

- This is a quality and reliability release; no new model or engine is required
  to benefit from it.
