# Release Notes — v3.20.0

## Overview

v3.20.0 adds a generic, on-device multimodal (image + text) runtime so that
HuggingFace `transformers`-native vision-language models can be installed and
queried entirely locally. The release also tightens engine isolation and grows
the test suite.

## Highlights

- **Generic vision-language runtime**
  - Load native multimodal checkpoints through a single, model-family-agnostic
    path using `AutoProcessor` and the appropriate `AutoModel*` class.
  - Image + text chat with both non-streaming and streaming responses.
  - OpenAI-compatible HTTP endpoint (`/api/multimodal/chat`, JSON and SSE) plus
    a capabilities endpoint; images may be supplied by URL, data URI or file.
  - Dedicated Electron IPC and a new "Vision Chat" workspace with model
    selection, image picker, and answer pane (English and Chinese UI).
- **Newly supported models**
  - SmolVLM-256M-Instruct (lightweight, ~0.5 GB runtime footprint)
  - Janus-Pro-7B (unified understanding model)
  - MiniCPM-V-4.6
- **Engine hardening**
  - The `transformers` engine is pinned to a known-good version with explicit
    supporting packages, and the runtime now initialises its numerical stack
    deterministically to prevent mixed-library conflicts.

## Compatibility

- Same system requirements as v3.19.x; settings and downloaded models carry
  over without migration.

## Verification

- Python test suite: 1620 passed.
- Renderer/JavaScript syntax and renderer unit tests: passing.
- Catalog schema and source-availability checks: passing.
- Real CPU inference checked with SmolVLM-256M on sample images.

## Notes

- Janus-Pro's autoregressive image-generation output is not exposed in this
  release; the current integration covers image understanding. It is planned
  for a future update.
