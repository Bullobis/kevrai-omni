import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { Logo } from "../components/Logo.jsx";
import { theme } from "../theme.js";

const PLATFORMS = ["Windows", "Linux", "macOS"];

export const SceneOutro = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const pop = spring({ frame: frame - 4, fps, config: { damping: 15, stiffness: 110 }, durationInFrames: 36 });
  const logoScale = interpolate(pop, [0, 1], [0.7, 1]);

  const titleOp = interpolate(frame, [28, 48], [0, 1], { extrapolateRight: "clamp" });
  const titleY = interpolate(frame, [28, 48], [16, 0], { extrapolateRight: "clamp" });
  const repoOp = interpolate(frame, [52, 72], [0, 1], { extrapolateRight: "clamp" });
  const platOp = interpolate(frame, [70, 90], [0, 1], { extrapolateRight: "clamp" });

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 24 }}>
        <div style={{ transform: `scale(${logoScale})` }}>
          <Logo size={120} />
        </div>

        <div style={{ opacity: titleOp, transform: `translateY(${titleY}px)`, textAlign: "center" }}>
          <h2 style={{ margin: 0, fontSize: 60, fontWeight: 800, color: theme.fg }}>
            全部开源 · <span style={{ color: theme.acc }}>免费本地运行</span>
          </h2>
        </div>

        <div
          style={{
            opacity: repoOp,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "16px 28px",
            borderRadius: 14,
            background: theme.panel,
            border: `1px solid rgba(76,169,122,0.5)`,
            fontFamily: theme.mono,
            fontSize: 26,
            color: theme.fg,
          }}
        >
          <span style={{ color: theme.mut }}>⭐</span>
          {data.repo}
        </div>

        <div style={{ opacity: platOp, display: "flex", gap: 12, alignItems: "center" }}>
          <span style={{ fontSize: 18, color: theme.mut }}>Releases 下载</span>
          {PLATFORMS.map((p) => (
            <span
              key={p}
              style={{
                padding: "8px 18px",
                borderRadius: 999,
                border: `1px solid ${theme.line}`,
                background: "rgba(255,255,255,0.04)",
                color: theme.fg,
                fontSize: 18,
              }}
            >
              {p}
            </span>
          ))}
        </div>

        <p style={{ position: "absolute", bottom: 40, fontSize: 15, color: theme.mut, fontFamily: theme.mono }}>
          Kevrai Omni v{data.version} · {data.dateLabel} · rendered with Remotion
        </p>
      </AbsoluteFill>
    </SceneFrame>
  );
};
