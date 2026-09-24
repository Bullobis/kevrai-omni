import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
} from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { theme } from "../theme.js";

export const SceneStats = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const stats = data.stats;

  const headOp = interpolate(frame, [6, 22], [0, 1], { extrapolateRight: "clamp" });
  const headY = interpolate(frame, [6, 22], [14, 0], { extrapolateRight: "clamp" });

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill
        style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 56 }}
      >
        <div style={{ opacity: headOp, transform: `translateY(${headY}px)`, textAlign: "center" }}>
          <h2 style={{ margin: 0, fontSize: 46, fontWeight: 700, color: theme.fg }}>
            一个安装包 · <span style={{ color: theme.acc }}>全部本地运行</span>
          </h2>
        </div>

        <div style={{ display: "flex", gap: 28 }}>
          {stats.map((s, i) => {
            const start = 22 + i * 6;
            const p = interpolate(frame, [start, start + 30], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const shown = Math.round(p * s.value);
            const op = interpolate(frame, [start, start + 14], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const y = interpolate(frame, [start, start + 14], [22, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const highlight = i === 3;
            return (
              <div
                key={i}
                style={{
                  opacity: op,
                  transform: `translateY(${y}px)`,
                  width: 240,
                  padding: "34px 18px",
                  borderRadius: 18,
                  background: theme.panel,
                  border: `1px solid ${highlight ? "rgba(76,169,122,0.5)" : theme.line}`,
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    fontSize: 92,
                    fontWeight: 800,
                    lineHeight: 1,
                    color: highlight ? theme.acc : theme.fg,
                    fontFamily: theme.mono,
                  }}
                >
                  {shown}
                </div>
                <div style={{ marginTop: 16, fontSize: 19, color: theme.mut }}>{s.label}</div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};
