import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { SceneFrame } from "../components/SceneFrame.jsx";
import { theme } from "../theme.js";

const CHIPS = [
  { icon: "🟢", name: "NVIDIA GPU · CUDA", detail: "驱动正常 · 24GB VRAM", ok: true },
  { icon: "🔴", name: "AMD GPU · ROCm", detail: "未检测到设备", ok: false },
  { icon: "🟠", name: "华为昇腾 · NPU", detail: "未检测到设备", ok: false },
];

export const SceneHardware = ({ durationInFrames, data }) => {
  const frame = useCurrentFrame();

  const panelP = interpolate(frame, [4, 22], [0, 1], { extrapolateRight: "clamp" });
  const panelY = interpolate(frame, [4, 22], [20, 0], { extrapolateRight: "clamp" });

  const gauge = interpolate(frame, [26, 64], [0, 0.72], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <SceneFrame durationInFrames={durationInFrames}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <SceneLabel frame={frame} />
        <div
          style={{
            opacity: panelP,
            transform: `translateY(${panelY}px)`,
            marginTop: 10,
            width: 1320,
            height: 600,
            borderRadius: 20,
            background: theme.panel,
            border: `1px solid ${theme.line}`,
            boxShadow: "0 34px 90px rgba(0,0,0,0.5)",
            display: "flex",
          }}
        >
          {/* left: chip detection */}
          <div style={{ flex: 1.15, padding: 38, borderRight: `1px solid ${theme.line}` }}>
            <h3 style={{ margin: "0 0 24px", fontSize: 24, color: theme.fg, fontWeight: 700 }}>
              自动芯片检测
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              {CHIPS.map((c, i) => (
                <ChipRow key={i} c={c} frame={frame} delay={24 + i * 16} />
              ))}
            </div>
          </div>

          {/* right: VRAM gauge */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 26 }}>
            <Gauge p={gauge} />
            <div style={{ display: "flex", gap: 26 }}>
              <Spec label="系统内存" value="64 GB" />
              <Spec label="磁盘" value="2 TB SSD" />
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </SceneFrame>
  );
};

const ChipRow = ({ c, frame, delay }) => {
  const scanning = frame >= delay && frame < delay + 18;
  const done = frame >= delay + 18;
  const op = interpolate(frame, [delay, delay + 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const x = interpolate(frame, [delay, delay + 14], [-18, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        opacity: op,
        transform: `translateX(${x}px)`,
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: 20,
        borderRadius: 14,
        background: theme.card,
        border: `1px solid ${done && c.ok ? "rgba(76,169,122,0.5)" : theme.line}`,
      }}
    >
      <span style={{ fontSize: 30 }}>{c.icon}</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 21, fontWeight: 600, color: theme.fg }}>{c.name}</div>
        <div style={{ fontSize: 15, color: theme.mut, marginTop: 3 }}>
          {done ? c.detail : scanning ? "扫描中…" : "等待检测"}
        </div>
      </div>
      {done && (
        <span
          style={{
            fontSize: 22,
            fontWeight: 800,
            color: c.ok ? theme.acc : theme.mut,
          }}
        >
          {c.ok ? "✓" : "—"}
        </span>
      )}
      {scanning && <Spinner frame={frame} />}
    </div>
  );
};

const Spinner = ({ frame }) => (
  <span
    style={{
      width: 24,
      height: 24,
      borderRadius: "50%",
      border: `3px solid ${theme.line}`,
      borderTopColor: theme.acc,
      display: "inline-block",
      transform: `rotate(${frame * 18}deg)`,
    }}
  />
);

const Gauge = ({ p }) => {
  const R = 96;
  const C = 2 * Math.PI * R;
  return (
    <div style={{ position: "relative", width: 250, height: 250 }}>
      <svg width={250} height={250} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={125} cy={125} r={R} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={16} />
        <circle
          cx={125}
          cy={125}
          r={R}
          fill="none"
          stroke={theme.acc}
          strokeWidth={16}
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={C * (1 - p)}
        />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 40, fontWeight: 800, color: theme.fg, fontFamily: theme.mono }}>
          {Math.round(p * 24)}
          <span style={{ fontSize: 19, color: theme.mut }}>/24 GB</span>
        </span>
        <span style={{ fontSize: 15, color: theme.mut, marginTop: 2 }}>显存自动管理</span>
      </div>
    </div>
  );
};

const Spec = ({ label, value }) => (
  <div style={{ textAlign: "center" }}>
    <div style={{ fontSize: 22, fontWeight: 700, color: theme.fg, fontFamily: theme.mono }}>{value}</div>
    <div style={{ fontSize: 14, color: theme.mut, marginTop: 3 }}>{label}</div>
  </div>
);

const SceneLabel = ({ frame }) => {
  const op = interpolate(frame, [4, 20], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 70, left: 90, opacity: op, display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 30 }}>⚡</span>
      <span style={{ fontSize: 30, fontWeight: 700, color: theme.fg }}>硬件体检</span>
      <span style={{ fontSize: 19, color: theme.mut }}>多芯片检测 · 显存自动管理 · 智能推荐</span>
    </div>
  );
};
