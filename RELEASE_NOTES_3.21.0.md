# Release Notes — v3.21.0

## Overview

v3.21.0 extends the on-device multimodal runtime from image **understanding**
to autoregressive image **generation**. Janus models can now create images
from a text prompt in addition to answering questions about pictures.

## Highlights

- **Text-to-image generation**
  - Autoregressive image-token sampling with classifier-free guidance, decoded
    to PNG through the model's visual tokenizer (VQVAE).
  - Configurable guidance strength, seed (for reproducible results), and number
    of images per prompt.
  - New HTTP endpoint that renders images, saves them to the task directory,
    and returns a manifest.
- **Workspace**
  - The multimodal workspace gains a mode switch between image understanding
    and text-to-image, with a result gallery (English and Chinese UI).
- **Models**
  - Janus-Pro-7B now exposes image generation.
  - Added Janus-Pro-1B, a smaller unified model for lower-memory machines.

## Compatibility

- Same system requirements as v3.20.x; settings and downloaded models carry
  over without migration.

## Verification

- Python test suite: 1630 passed.
- Renderer/JavaScript syntax and renderer unit tests: passing.
- Catalog schema and source-availability checks: passing.
- Image-understanding path verified with real inference; the image-decoding
  pipeline is covered by deterministic tests. Generating with the 7B/1B models
  requires a CUDA-class GPU and is not run on the CPU-only build host.

## Notes

- Image generation is most responsive on a GPU; CPU use is supported but slow.
