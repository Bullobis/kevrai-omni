// Static regression for the capability-driven video form.
"use strict";
import test from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");

test("video form exposes per-model capability controls", () => {
  const html = read("renderer/index.html");
  assert.match(html, /id="ltx-model-capability"/);
  assert.match(html, /id="ltx-prompt-field"/);
  assert.match(html, /id="ltx-negative-field"/);
});

test("video form applies model capability on model switch", () => {
  const js = read("renderer/modules/ltx.js");
  assert.match(js, /function applyModelCapability\(/);
  assert.match(js, /ltx-model.*addEventListener\("change", applyModelCapability\)/s);
  assert.match(js, /modelCap\?\.prompt !== "none"/);
  assert.match(js, /modelCap\?\.image\?\.required/);
});

test("SVD is declared as image-only", () => {
  const py = fs.readFileSync(
    path.join(root, "python/app/ltx_runtime.py"), "utf8");
  assert.match(py, /stabilityai\/stable-video-diffusion-img2vid-xt/);
  assert.match(py, /PROMPT_NONE_MODELS/);
});

test("market detail renders capability manifest", () => {
  const js = read("renderer/modules/models.js");
  assert.match(js, /function renderCapability\(/);
  assert.match(js, /capabilityTitle/);
  assert.match(js, /cap-task/); 
});
