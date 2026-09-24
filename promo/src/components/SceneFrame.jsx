import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";

// Wraps every scene: fade in/out at the ends plus a small settle-up on entry.
export const SceneFrame = ({ children, durationInFrames, fade = 14, settle = 16 }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, fade, durationInFrames - fade, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const y = interpolate(frame, [0, fade], [settle, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ opacity, transform: `translateY(${y}px)` }}>
      {children}
    </AbsoluteFill>
  );
};
