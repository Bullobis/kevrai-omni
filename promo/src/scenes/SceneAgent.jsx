import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { WindowFrame } from "../components/WindowFrame.jsx";
import { theme } from "../theme.js";

const SKILLS = ["核心", "模型检索", "本机环境", "短剧工坊", "写作工坊", "提示词"];

export const SceneAgent = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 18, stiffness: 90 }, durationInFrames: 24 });
  const scale = interpolate(enter, [0, 1], [0.94, 1]);

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <SceneLabel frame={frame} />
        <div style={{ transform: `scale(${scale})`, marginTop: 14 }}>
          <WindowFrame title="🤖 Kevrai Agent" width={1340} height={660}>
            {/* skill chips */}
            <div style={{ display: "flex", gap: 9, padding: "14px 22px", borderBottom: `1px solid ${theme.line}`, flexShrink: 0, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, color: theme.mut, marginRight: 4, alignSelf: "center" }}>🧩 技能库</span>
              {SKILLS.map((s, i) => (
                <span
                  key={s}
                  style={{
                    fontSize: 13,
                    padding: "4px 11px",
                    borderRadius: 8,
                    background: i === 0 ? "rgba(76,169,122,0.18)" : "rgba(255,255,255,0.04)",
                    border: `1px solid ${i === 0 ? "rgba(76,169,122,0.5)" : theme.line}`,
                    color: i === 0 ? theme.acc : theme.mut,
                  }}
                >
                  {s}
                </span>
              ))}
            </div>

            {/* messages */}
            <div style={{ flex: 1, padding: 24, display: "flex", flexDirection: "column", gap: 16, overflow: "hidden" }}>
              <Bubble side="right" frame={frame} start={14}>
                帮我看看这台机器能跑什么视频模型？
              </Bubble>

              <TypeLine frame={frame} start={32} text={data.agentLines[0]} style={{ color: theme.mut, fontStyle: "italic", fontSize: 18 }} prefix="💭 " />
              <ToolCall frame={frame} start={52} text={data.agentLines[1]} />
              <TypeLine frame={frame} start={72} text={data.agentLines[2]} style={{ color: theme.acc2, fontSize: 18, fontFamily: theme.mono }} prefix="📋 " />

              <Bubble side="left" frame={frame} start={92} final>
                {data.agentLines[3]}
              </Bubble>
            </div>
          </WindowFrame>
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};

const TypeLine = ({ frame, start, text, style, prefix }) => {
  const n = interpolate(frame, [start, start + text.length * 0.75], [0, text.length], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{ opacity: frame >= start ? 1 : 0, fontSize: 18, ...style }}>
      {prefix}
      {text.slice(0, Math.round(n))}
    </div>
  );
};

const ToolCall = ({ frame, start, text }) => {
  const op = interpolate(frame, [start, start + 14], [0, 1], { extrapolateRight: "clamp" });
  const x = interpolate(frame, [start, start + 14], [-12, 0], { extrapolateRight: "clamp" });
  return (
    <div
      style={{
        opacity: op,
        transform: `translateX(${x}px)`,
        alignSelf: "flex-start",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "10px 16px",
        borderRadius: 10,
        background: "rgba(217,160,91,0.12)",
        border: "1px solid rgba(217,160,91,0.45)",
        fontFamily: theme.mono,
        fontSize: 17,
        color: theme.warn,
      }}
    >
      🔧 {text}
    </div>
  );
};

const Bubble = ({ children, side, frame, start, final }) => {
  const op = interpolate(frame, [start, start + 16], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [start, start + 16], [14, 0], { extrapolateRight: "clamp" });
  const isRight = side === "right";
  return (
    <div
      style={{
        opacity: op,
        transform: `translateY(${y}px)`,
        alignSelf: isRight ? "flex-end" : "flex-start",
        maxWidth: "72%",
        padding: "14px 20px",
        borderRadius: 16,
        fontSize: 19,
        lineHeight: 1.45,
        background: final ? "rgba(76,169,122,0.16)" : isRight ? theme.popover : theme.card,
        border: `1px solid ${final ? "rgba(76,169,122,0.5)" : theme.line}`,
        color: final ? theme.acc : theme.fg,
        fontWeight: final ? 600 : 400,
      }}
    >
      {children}
    </div>
  );
};

const SceneLabel = ({ frame }) => {
  const op = interpolate(frame, [4, 20], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 60, left: 90, opacity: op, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 30 }}>🤖</span>
      <span style={{ fontSize: 30, fontWeight: 700, color: theme.fg }}>Kevrai Agent</span>
      <span style={{ fontSize: 19, color: theme.mut }}>ReAct 推理 · 工具调用 · 可插拔技能 · 本地记忆</span>
    </div>
  );
};
