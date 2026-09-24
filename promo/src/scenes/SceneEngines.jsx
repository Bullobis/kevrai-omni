import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { theme } from "../theme.js";

export const SceneEngines = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();
  const headP = interpolate(frame, [4, 22], [0, 1], { extrapolateRight: "clamp" });

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <SceneLabel frame={frame} />
        <div style={{ marginTop: 4, width: 1320, opacity: headP }}>
          {/* big cards */}
          <div style={{ display: "flex", gap: 22, marginBottom: 26 }}>
            <BigCard frame={frame} start={12} icon="⬢" title="llama.cpp" sub="GGUF 推理 · 本地服务运行中" running />
            <BigCard frame={frame} start={22} icon="⬡" title="MNN" sub="轻量通用 · 端侧推理加速" />
            <MusicCard frame={frame} start={32} />
          </div>

          {/* engine wall */}
          <div
            style={{
              background: theme.panel,
              border: `1px solid ${theme.line}`,
              borderRadius: 18,
              padding: 26,
            }}
          >
            <div style={{ fontSize: 18, color: theme.mut, marginBottom: 16 }}>
              引擎市场 · 按需下载，共 <span style={{ color: theme.acc, fontWeight: 700 }}>{data.totals.engines}</span> 个
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 11 }}>
              {data.engines.map((name, i) => (
                <EngineChip key={i} frame={frame} start={46 + i * 4} name={name} />
              ))}
              <span style={{ alignSelf: "center", fontSize: 15, color: theme.mut, marginLeft: 4 }}>
                …等 {data.totals.engines} 个引擎
              </span>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};

const BigCard = ({ frame, start, icon, title, sub, running }) => {
  const op = interpolate(frame, [start, start + 18], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [start, start + 18], [22, 0], { extrapolateRight: "clamp" });
  return (
    <div
      style={{
        opacity: op,
        transform: `translateY(${y}px)`,
        flex: 1,
        height: 200,
        borderRadius: 18,
        background: theme.panel,
        border: `1px solid ${running ? "rgba(76,169,122,0.5)" : theme.line}`,
        padding: 26,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <span style={{ fontSize: 44, color: theme.acc }}>{icon}</span>
        {running && (
          <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 15, color: theme.acc }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: theme.acc }} />
            运行中
          </span>
        )}
      </div>
      <div>
        <div style={{ fontSize: 27, fontWeight: 700, color: theme.fg }}>{title}</div>
        <div style={{ fontSize: 16, color: theme.mut, marginTop: 5 }}>{sub}</div>
      </div>
    </div>
  );
};

const MusicCard = ({ frame, start }) => {
  const op = interpolate(frame, [start, start + 18], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [start, start + 18], [22, 0], { extrapolateRight: "clamp" });
  return (
    <div
      style={{
        opacity: op,
        transform: `translateY(${y}px)`,
        flex: 1,
        height: 200,
        borderRadius: 18,
        background: theme.panel,
        border: `1px solid rgba(111,178,210,0.5)`,
        padding: 26,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
      }}
    >
      <div style={{ fontSize: 18, color: theme.mut }}>🎵 MiniMax-Music3</div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 5, height: 84 }}>
        {Array.from({ length: 22 }).map((_, i) => {
          const h = 22 + Math.abs(Math.sin(frame * 0.18 + i * 0.55)) * 60;
          return (
            <span
              key={i}
              style={{
                flex: 1,
                height: h,
                borderRadius: 3,
                background: `linear-gradient(180deg,${theme.acc2},${theme.acc})`,
              }}
            />
          );
        })}
      </div>
      <div style={{ fontSize: 15, color: theme.mut }}>立体声 · 32kHz · 最长 5 分钟</div>
    </div>
  );
};

const EngineChip = ({ frame, start, name }) => {
  const op = interpolate(frame, [start, start + 9], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = interpolate(frame, [start, start + 9], [0.85, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <span
      style={{
        opacity: op,
        transform: `scale(${scale})`,
        padding: "8px 15px",
        borderRadius: 9,
        fontSize: 15,
        color: theme.fg,
        background: theme.card,
        border: `1px solid ${theme.line}`,
      }}
    >
      {name}
    </span>
  );
};

const SceneLabel = ({ frame }) => {
  const op = interpolate(frame, [4, 20], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 64, left: 90, opacity: op, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 30 }}>⬢</span>
      <span style={{ fontSize: 30, fontWeight: 700, color: theme.fg }}>双引擎 · 音乐生成</span>
      <span style={{ fontSize: 19, color: theme.mut }}>llama.cpp · MNN · MiniMax-Music3</span>
    </div>
  );
};
