import React from "react";
import { AbsoluteFill } from "remotion";
import { theme } from "../theme.js";

// Neutral dark base, a faint dot grid for depth, and two very restrained
// brand glows (green + indigo). Kept subtle so content stays the focus.
export const Background = () => (
  <AbsoluteFill style={{ backgroundColor: theme.bg }}>
    <AbsoluteFill
      style={{
        backgroundImage:
          "radial-gradient(rgba(255,255,255,0.045) 1px, transparent 1px)",
        backgroundSize: "34px 34px",
      }}
    />
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(640px 640px at 84% -12%, rgba(76,169,122,0.12), transparent 62%), radial-gradient(720px 720px at 6% 112%, rgba(124,92,255,0.13), transparent 62%)",
      }}
    />
  </AbsoluteFill>
);
