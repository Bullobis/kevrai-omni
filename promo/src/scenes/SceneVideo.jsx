import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { theme } from "../theme.js";

const STARS = [
  [12, 22], [28, 60], [44, 18], [62, 48], [78, 26], [88, 66], [20, 80],
  [54, 74], [70, 84], [36, 38], [8, 52], [92, 40],
];

export const SceneVideo = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const panelP = interpolate(frame, [4, 22], [0, 1], { extrapolateRight: "clamp" });
  const panelY = interpolate(frame, [4, 22], [20, 0], { extrapolateRight: "clamp" });

  const progress = interpolate(frame, [32, 98], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const done = frame >= 98;

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <SceneLabel frame={frame} />
        <div
          style={{
            opacity: panelP,
            transform: `translateY(${panelY}px)`,
            marginTop: 8,
            width: 1360,
            height: 620,
            borderRadius: 20,
            background: theme.panel,
            border: `1px solid ${theme.line}`,
            boxShadow: "0 34px 90px rgba(0,0,0,0.5)",
            display: "flex",
          }}
        >
          {/* left: form */}
          <div style={{ width: 560, padding: 34, borderRight: `1px solid ${theme.line}`, display: "flex", flexDirection: "column", gap: 16 }}>
            <Field label="生成模式" value="文生视频 (T2V) ▾" />
            <Field label="质量预设" value="高质量 · 16GB VRAM ▾" accent />
            <div>
              <div style={{ fontSize: 15, color: theme.mut, marginBottom: 8 }}>提示词 Prompt</div>
              <div
                style={{
                  background: theme.bg,
                  border: `1px solid ${theme.line}`,
                  borderRadius: 10,
                  padding: 14,
                  fontSize: 17,
                  color: theme.fg,
                  lineHeight: 1.4,
                  minHeight: 78,
                }}
              >
                一只猫在月球上弹钢琴，电影感，4K，柔和光影
              </div>
            </div>
            <div style={{ display: "flex", gap: 12 }}>
              <Mini label="帧数" value="97" />
              <Mini label="CFG" value="3.0" />
              <Mini label="FPS" value="24" />
            </div>

            <div style={{ marginTop: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontSize: 16, color: done ? theme.acc : theme.mut }}>
                  {done ? "✓ 渲染完成" : "渲染中…"}
                </span>
                <span style={{ fontSize: 16, color: theme.fg, fontFamily: theme.mono }}>{Math.round(progress * 100)}%</span>
              </div>
              <div style={{ height: 12, borderRadius: 999, background: theme.bg, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${progress * 100}%`,
                    height: "100%",
                    background: `linear-gradient(90deg,${theme.acc},${theme.acc2})`,
                    borderRadius: 999,
                  }}
                />
              </div>
            </div>
          </div>

          {/* right: preview */}
          <div style={{ flex: 1, padding: 34, display: "flex", flexDirection: "column" }}>
            <div
              style={{
                flex: 1,
                borderRadius: 14,
                overflow: "hidden",
                position: "relative",
                background: "#0a0d1a",
                border: `1px solid ${done ? "rgba(76,169,122,0.5)" : theme.line}`,
              }}
            >
              <div style={{ position: "absolute", inset: 0, filter: `blur(${(1 - progress) * 16}px)`, transform: `scale(${1 + (1 - progress) * 0.08})` }}>
                <SpaceScene frame={frame} />
              </div>
              {/* noise / settling veil */}
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  opacity: (1 - progress) * 0.85,
                  background:
                    "repeating-conic-gradient(rgba(255,255,255,0.10) 0% 0.05%, rgba(0,0,0,0.10) 0.05% 0.1%)",
                }}
              />
              {done && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <span style={{ width: 84, height: 84, borderRadius: "50%", background: "rgba(0,0,0,0.55)", border: `2px solid ${theme.acc}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 34, color: theme.acc, paddingLeft: 6 }}>
                    ▶
                  </span>
                </div>
              )}
            </div>
            <div style={{ marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 15, color: theme.mut, fontFamily: theme.mono }}>output_001.mp4 · 768×432</span>
              <span style={{ fontSize: 15, color: theme.acc }}>📁 打开目录</span>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};

const SpaceScene = ({ frame }) => {
  const moonX = 30 + (frame % 240) * 0.18;
  return (
    <AbsoluteFill style={{ background: "linear-gradient(180deg,#0b1026 0%,#1a1740 55%,#2a1f4d 100%)" }}>
      {STARS.map(([x, y], i) => (
        <span
          key={i}
          style={{
            position: "absolute",
            left: `${x}%`,
            top: `${y}%`,
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: "#fff",
            opacity: 0.4 + 0.6 * Math.abs(Math.sin(frame * 0.08 + i)),
          }}
        />
      ))}
      <div
        style={{
          position: "absolute",
          left: `${moonX}%`,
          top: "34%",
          width: 150,
          height: 150,
          borderRadius: "50%",
          background: "radial-gradient(circle at 35% 35%,#fdfdfd,#c9c6d6 60%,#8a86a0)",
          boxShadow: "0 0 60px rgba(255,255,255,0.25)",
        }}
      />
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 90, background: "linear-gradient(180deg,transparent,rgba(120,90,220,0.35))" }} />
    </AbsoluteFill>
  );
};

const Field = ({ label, value, accent }) => (
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", borderRadius: 10, background: theme.bg, border: `1px solid ${accent ? "rgba(76,169,122,0.4)" : theme.line}` }}>
    <span style={{ fontSize: 16, color: theme.mut }}>{label}</span>
    <span style={{ fontSize: 16, color: accent ? theme.acc : theme.fg }}>{value}</span>
  </div>
);

const Mini = ({ label, value }) => (
  <div style={{ flex: 1, background: theme.bg, border: `1px solid ${theme.line}`, borderRadius: 10, padding: "10px 12px", textAlign: "center" }}>
    <div style={{ fontSize: 19, color: theme.fg, fontFamily: theme.mono, fontWeight: 700 }}>{value}</div>
    <div style={{ fontSize: 13, color: theme.mut, marginTop: 2 }}>{label}</div>
  </div>
);

const SceneLabel = ({ frame }) => {
  const op = interpolate(frame, [4, 20], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 60, left: 90, opacity: op, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 30 }}>🎥</span>
      <span style={{ fontSize: 30, fontWeight: 700, color: theme.fg }}>LTX-2.5 视频生成</span>
      <span style={{ fontSize: 19, color: theme.mut }}>文生 / 图生 · 5 档显存预设 · 近实时</span>
    </div>
  );
};
