import { Config } from "@remotion/cli/config";
import fs from "node:fs";

// Pick the first Chromium/Chrome that actually exists, so the render never
// tries to download an extra browser when the system already has one.
const candidates = [
  process.env.REMOTION_BROWSER,
  "/usr/local/bin/chromium",
  "/usr/local/bin/chromium-browser",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
].filter(Boolean);

const found = candidates.find((p) => fs.existsSync(p));
if (found) {
  Config.setBrowserExecutable(found);
}

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setPixelFormat("yuv420p");
Config.setCodec("h264");
