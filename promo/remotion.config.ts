import { Config } from "@remotion/cli/config";

// Headless-friendly defaults. Override on the CLI as needed.
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setConcurrency(2);
