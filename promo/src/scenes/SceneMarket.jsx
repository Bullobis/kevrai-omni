import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { WindowFrame } from "../components/WindowFrame.jsx";
import { theme } from "../theme.js";

const QUERY = "video";

export const SceneMarket = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 90, mass: 0.9 },
    durationInFrames: 26,
  });
  const winScale = interpolate(enter, [0, 1], [0.94, 1]);

  const typedCount = Math.round(
    interpolate(frame, [82, 114], [0, QUERY.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  const typed = QUERY.slice(0, typedCount);
  const filtering = frame > 100;

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <SceneLabel frame={frame} />
        <div style={{ transform: `scale(${winScale})`, marginTop: 18 }}>
          <WindowFrame title="Kevrai Omni — 模型市场" width={1460} height={580}>
            {/* toolbar */}
            <div
              style={{
                display: "flex",
                gap: 14,
                alignItems: "center",
                padding: "0 22px",
                height: 64,
                borderBottom: `1px solid ${theme.line}`,
                flexShrink: 0,
              }}
            >
              <Pill>全部分类 ▾</Pill>
              <Pill>相关度 ▾</Pill>
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  background: theme.bg,
                  border: `1px solid ${filtering ? "rgba(76,169,122,0.55)" : theme.line}`,
                  borderRadius: 10,
                  height: 40,
                  padding: "0 14px",
                  gap: 8,
                }}
              >
                <span style={{ color: theme.mut }}>🔍</span>
                <span style={{ color: theme.fg, fontSize: 17, fontFamily: theme.mono }}>{typed}</span>
                <Caret active={frame > 82} frame={frame} />
              </div>
              <span style={{ color: theme.mut, fontSize: 16, fontFamily: theme.mono }}>
                {filtering ? "1 条" : `${data.featured.length} 条`}
              </span>
            </div>

            {/* grid */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4,1fr)",
                gap: 18,
                padding: 22,
                flex: 1,
              }}
            >
              {data.featured.map((m, i) => (
                <ModelCard key={i} m={m} frame={frame} index={i} filtering={filtering} />
              ))}
            </div>
          </WindowFrame>
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};

const ModelCard = ({ m, frame, index, filtering }) => {
  const delay = 26 + index * 7;
  const enter = interpolate(frame, [delay, delay + 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const y = interpolate(frame, [delay, delay + 20], [26, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const isMatch = m.category === "video";
  const dim = filtering && !isMatch;
  const active = filtering && isMatch;
  const pulse = active ? 0.5 + 0.5 * Math.sin(frame * 0.22) : 0;

  return (
    <div
      style={{
        opacity: dim ? 0.16 : enter,
        transform: `translateY(${y}px) scale(${active ? 1.03 : 1})`,
        background: theme.card,
        border: `1px solid ${active ? `rgba(76,169,122,${0.5 + pulse * 0.5})` : theme.line}`,
        borderRadius: 14,
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ fontSize: 19, fontWeight: 700, color: theme.fg, lineHeight: 1.25, minHeight: 46 }}>
        {m.name}
      </div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
        {m.size !== null && <MiniBadge>{m.size} GB</MiniBadge>}
        <MiniBadge>{m.license.split(" ")[0]}</MiniBadge>
        <MiniBadge accent>{m.categoryLabel}</MiniBadge>
      </div>
      <div
        style={{
          fontSize: 14,
          color: theme.mut,
          lineHeight: 1.4,
          flex: 1,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {m.description}
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <span
          style={{
            padding: "7px 18px",
            borderRadius: 9,
            fontSize: 15,
            fontWeight: 700,
            color: active ? "#06110c" : "#06110c",
            background: active ? `rgba(76,169,122,${0.75 + pulse * 0.25})` : theme.acc,
          }}
        >
          安装
        </span>
      </div>
    </div>
  );
};

const SceneLabel = ({ frame }) => {
  const op = interpolate(frame, [4, 20], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 70, left: 90, opacity: op, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 30 }}>◇</span>
      <span style={{ fontSize: 30, fontWeight: 700, color: theme.fg }}>模型市场</span>
      <span style={{ fontSize: 19, color: theme.mut }}>多源测速 · 断点续传 · 双源镜像</span>
    </div>
  );
};

const Pill = ({ children }) => (
  <span
    style={{
      padding: "9px 14px",
      borderRadius: 10,
      background: theme.bg,
      border: `1px solid ${theme.line}`,
      color: theme.fg,
      fontSize: 15,
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </span>
);

const MiniBadge = ({ children, accent }) => (
  <span
    style={{
      padding: "3px 9px",
      borderRadius: 7,
      fontSize: 12,
      fontFamily: theme.mono,
      color: accent ? theme.acc : theme.mut,
      border: `1px solid ${accent ? "rgba(76,169,122,0.4)" : theme.line}`,
      background: "rgba(255,255,255,0.03)",
    }}
  >
    {children}
  </span>
);

const Caret = ({ active, frame }) => {
  if (!active) return null;
  const blink = Math.floor(frame / 9) % 2 === 0;
  return (
    <span
      style={{
        width: 2,
        height: 20,
        background: theme.acc,
        opacity: blink ? 1 : 0.15,
        display: "inline-block",
      }}
    />
  );
};
