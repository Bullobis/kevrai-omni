# Release Notes — v3.22.0

## Overview

v3.22.0 deepens the agent, the product's core assistant. A new opt-in skill
lets the agent directly run the on-device media engines and chain them into
workflows, instead of only planning around them. This release also follows a
full cross-platform quality pass.

## Highlights

- **Agent media-engine skill**
  - Enable "Local media engines" in the agent skill library to give the agent
    six new tools:
    - transcribe an audio/video file to text;
    - compute text embeddings (vectors saved to a file);
    - separate a song into stems;
    - synthesize speech from text;
    - answer questions about an image;
    - generate images from a text prompt.
  - Tool outputs are returned as file paths, so steps can be chained — for
    example transcribe audio, then embed the text; or draft text, then speak it.
  - If an engine is not installed, the agent points to the page that installs it.
- **Quality**
  - Full regression across the Python backend, renderer, packaging smoke and
    on-screen panels; dark/light themes and empty/error states reviewed.

## Compatibility

- Same system requirements as v3.21.x; settings, sessions and downloaded
  models carry over without migration.

## Verification

- Python test suite: 1646 passed.
- Renderer unit tests and JavaScript syntax checks: passing.
- ruff: clean.
- The new skill was verified on-screen: it appears in the skill library, can be
  enabled, and the active tool count updates accordingly.

## Notes

- Media tools run fully on-device; GPU acceleration is recommended for image
  generation and large multimodal models.
