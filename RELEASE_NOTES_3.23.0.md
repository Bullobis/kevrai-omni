# Release Notes — v3.23.0

## Overview

v3.23.0 makes the agent autonomous. Until now the agent could call tools but
its multi-step reasoning depended on a rule-based path. This release lets you
pick a local model as the agent's "brain". That model understands the goal,
plans the steps, chooses and runs tools, reads the file-based intermediate
results, and continues until it can summarize — fully on-device.

## Highlights

- **Local brain for the agent**
  - A new brain selector in the agent panel lists small, CPU-friendly models
    (SmolLM2 135M / 360M, Qwen3 0.6B). The choice is saved and restored.
  - The brain drives the existing ReAct loop: it plans, emits the next tool
    call, observes the result, and chains further steps, then gives a final
    answer.
  - Selecting "Rule mode" keeps the previous deterministic behavior.
- **Step-by-step visibility and control**
  - Each tool call is shown with its parameters, a summary of the result, and
    chips that open generated files (images, audio, data) directly.
  - A Stop button cooperatively cancels a run; long tasks can be interrupted
    between steps.
- **Reliability**
  - A discipline guard rejects a conclusion produced before any tool has run on
    a task that needs data, and asks the model to fetch real information rather
    than guess values. After a bounded number of reminders the answer is
    accepted, so the loop cannot spin.
  - Per-turn token budget is bounded to keep latency predictable on CPU.

## Compatibility

- Same system requirements as v3.22.x; settings, sessions and downloaded
  models carry over without migration.

## Verification

- Python test suite: 1666 passed (including new brain-routing and loop-guard
  tests).
- JavaScript syntax checks: 51 files passed; renderer unit tests and packaging
  smoke tests: passing.
- ruff: clean.
- A real brain was loaded and generated on-device, producing factually correct
  text. Multi-step runs on a low-power CPU are slow but complete; the loop
  logic is also covered by deterministic tests.

## Notes

- Larger brains give better planning; on a CPU start with a 135M/360M model and
  use a GPU for heavier models.
