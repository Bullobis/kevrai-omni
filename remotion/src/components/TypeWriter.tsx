import React from "react";
import { interpolate, useCurrentFrame } from "remotion";

export const TypeWriter: React.FC<{
  text: string;
  startFrame: number;
  cps?: number; // 每秒字符数
  fps?: number;
  style?: React.CSSProperties;
}> = ({ text, startFrame, cps = 8, fps = 30, style }) => {
  const frame = useCurrentFrame();
  const localFrame = Math.max(0, frame - startFrame);
  const charsPerFrame = cps / fps;
  const charCount = Math.min(
    text.length,
    Math.floor(localFrame * charsPerFrame)
  );
  const shown = text.slice(0, charCount);
  // 光标闪烁
  const cursorOpacity = interpolate(frame % 30, [0, 15, 30], [1, 0, 1], {
    extrapolateRight: "clamp",
  });
  return (
    <div style={{ ...style, fontFamily: "sans-serif" }}>
      <span>{shown}</span>
      <span style={{ opacity: cursorOpacity, marginLeft: 4 }}>|</span>
    </div>
  );
};
