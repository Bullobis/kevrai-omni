import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { Logo } from "../components/Logo.jsx";
import { theme } from "../theme.js";

const CHIPS = ["LLM", "TTS", "Image", "Video", "3D", "Audio", "SR", "Agent"];

export const SceneIntro = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const pop = spring({
    frame: frame - 4,
    fps,
    config: { damping: 14, stiffness: 120, mass: 0.9 },
    durationInFrames: 42,
  });
  const logoScale = interpolate(pop, [0, 1], [0.72, 1]);

  const titleOp = interpolate(frame, [24, 42], [0, 1], { extrapolateRight: "clamp" });
  const titleY = interpolate(frame, [24, 42], [16, 0], { extrapolateRight: "clamp" });
  const subOp = interpolate(frame, [38, 52], [0, 1], { extrapolateRight: "clamp" });

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill
        style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 22 }}
      >
        <div style={{ transform: `scale(${logoScale})` }}>
          <Logo size={168} />
        </div>

        <div style={{ opacity: titleOp, transform: `translateY(${titleY}px)`, textAlign: "center" }}>
          <h1
            style={{
              margin: 0,
              fontSize: 76,
              fontWeight: 800,
              letterSpacing: 1,
              color: theme.fg,
            }}
          >
            Kevrai <span style={{ color: theme.acc }}>Omni</span>
          </h1>
        </div>

        <p
          style={{
            opacity: subOp,
            margin: 0,
            fontSize: 27,
            color: theme.mut,
            letterSpacing: 0.5,
          }}
        >
          一站式本地 AI 工作站 · One-stop local AI workstation
        </p>

        <div style={{ display: "flex", gap: 12, marginTop: 14, opacity: subOp }}>
          {CHIPS.map((c, i) => {
            const op = interpolate(frame, [54 + i * 3, 64 + i * 3], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const y = interpolate(frame, [54 + i * 3, 64 + i * 3], [10, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return (
              <span
                key={c}
                style={{
                  opacity: op,
                  transform: `translateY(${y}px)`,
                  padding: "8px 16px",
                  borderRadius: 999,
                  border: `1px solid ${theme.line}`,
                  background: "rgba(255,255,255,0.04)",
                  color: theme.fg,
                  fontSize: 19,
                  fontWeight: 500,
                }}
              >
                {c}
              </span>
            );
          })}
        </div>
      </AbsoluteFill>

      <VersionTag frame={frame} data={data} />
    </SceneFrame>
  );
};

const VersionTag = ({ frame, data }) => {
  const op = interpolate(frame, [70, 88], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div
      style={{
        position: "absolute",
        top: 40,
        right: 48,
        opacity: op,
        textAlign: "right",
        fontFamily: theme.mono,
      }}
    >
      <div style={{ color: theme.acc, fontSize: 20, fontWeight: 700 }}>v{data.version}</div>
      <div style={{ color: theme.mut, fontSize: 16, marginTop: 2 }}>{data.dateLabel}</div>
    </div>
  );
};
