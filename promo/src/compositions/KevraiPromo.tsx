import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  staticFile,
  Easing,
} from "remotion";

/* ---------------- Shared background ---------------- */

const FloatingDots: React.FC<{ seed?: number }> = ({ seed = 0 }) => {
  const frame = useCurrentFrame();
  const dots = React.useMemo(
    () =>
      Array.from({ length: 28 }, (_, i) => ({
        left: (i * 137.5) % 100,
        top: (i * 61.8) % 100,
        size: 2 + ((i * 7) % 5),
        // seed（variant）让每次定时重渲染的粒子动态略有差异
        speed: 0.3 + (((i * 13) + seed * 7) % 10) / 18,
        hue: 200 + (((i * 37) + seed * 23) % 80),
        drift: ((i * 53) + seed * 31) % 100,
      })),
    [seed]
  );
  return (
    <AbsoluteFill>
      {dots.map((d, i) => {
        const y = interpolate(
          (frame * d.speed + d.drift * 10) % 120,
          [0, 120],
          [110, -10]
        );
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: `${d.left}%`,
              top: `${y}%`,
              width: d.size,
              height: d.size,
              borderRadius: "50%",
              background: `hsla(${d.hue}, 90%, 70%, 0.55)`,
              boxShadow: `0 0 ${d.size * 3}px hsla(${d.hue}, 90%, 70%, 0.8)`,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

const Background: React.FC<{ children?: React.ReactNode; variant?: number }> = ({
  children,
  variant = 0,
}) => {
  return (
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(1200px 700px at 20% 10%, #1b2447 0%, transparent 60%)," +
          "radial-gradient(1000px 600px at 85% 90%, #2a1a4a 0%, transparent 55%)," +
          "linear-gradient(135deg, #070b18 0%, #0d1226 50%, #141033 100%)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        color: "#e8ecff",
      }}
    >
      <FloatingDots seed={variant} />
      {children}
    </AbsoluteFill>
  );
};

/* ---------------- Fade wrapper ---------------- */

const Fade: React.FC<{
  children: React.ReactNode;
  in?: number; // frames to fade in
  out?: number; // frames to fade out at end
  duration: number;
}> = ({ children, in: fin = 15, out = 15, duration }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, fin, duration - out, duration],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

/* ---------------- Screenshot card ---------------- */

const ShotCard: React.FC<{
  src: string;
  enterFrom: "right" | "left";
  duration: number;
  width?: number;
}> = ({ src, enterFrom, duration, width = 1180 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 200, stiffness: 90 } });
  const x = interpolate(
    s,
    [0, 1],
    [enterFrom === "right" ? 400 : -400, 0]
  );
  const scale = interpolate(s, [0, 1], [0.92, 1]);
  const height = Math.round((width * 900) / 1380);
  return (
    <AbsoluteFill
      style={{ justifyContent: "center", alignItems: "center", transform: `translateX(${x}px) scale(${scale})` }}
    >
      <div
        style={{
          width,
          height,
          borderRadius: 20,
          overflow: "hidden",
          boxShadow:
            "0 30px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(120,160,255,0.25), 0 0 60px rgba(80,120,255,0.25)",
          border: "1px solid rgba(140,180,255,0.3)",
        }}
      >
        <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </div>
    </AbsoluteFill>
  );
};

/* ---------------- Caption ---------------- */

const Caption: React.FC<{
  title: string;
  subtitle?: string;
  y?: number;
}> = ({ title, subtitle, y = 880 }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [10, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ty = interpolate(frame, [10, 30], [20, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  return (
    <AbsoluteFill style={{ top: y, alignItems: "center", opacity, transform: `translateY(${ty}px)` }}>
      <div
        style={{
          fontSize: 52,
          fontWeight: 700,
          letterSpacing: 1,
          background: "linear-gradient(90deg,#cfe0ff,#8fb0ff)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          textShadow: "0 0 30px rgba(120,160,255,0.3)",
        }}
      >
        {title}
      </div>
      {subtitle ? (
        <div style={{ marginTop: 14, fontSize: 26, color: "#9fb0d8", letterSpacing: 2 }}>
          {subtitle}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/* ---------------- Scenes ---------------- */

const Intro: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 180, stiffness: 80 } });
  const scale = interpolate(s, [0, 1], [0.7, 1]);
  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const nameOpacity = interpolate(frame, [25, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subOpacity = interpolate(frame, [45, 70], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [duration - 15, duration], [1, 0], {
    extrapolateLeft: "clamp",
  });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: fadeOut }}>
      <Img
        src={staticFile("logo.png")}
        style={{
          width: 260,
          height: 260,
          transform: `scale(${scale})`,
          opacity,
          filter: "drop-shadow(0 0 40px rgba(120,160,255,0.6))",
        }}
      />
      <div
        style={{
          marginTop: 40,
          fontSize: 84,
          fontWeight: 800,
          letterSpacing: 2,
          opacity: nameOpacity,
          background: "linear-gradient(90deg,#ffffff,#9db8ff)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        }}
      >
        Kevrai Omni
      </div>
      <div style={{ marginTop: 18, fontSize: 32, color: "#9fb0d8", letterSpacing: 8, opacity: subOpacity }}>
        本 地 AI 工 作 站
      </div>
    </AbsoluteFill>
  );
};

const ShotScene: React.FC<{
  src: string;
  title: string;
  subtitle: string;
  duration: number;
  enterFrom?: "right" | "left";
}> = ({ src, title, subtitle, duration, enterFrom = "right" }) => {
  return (
    <Fade duration={duration}>
      <ShotCard src={src} enterFrom={enterFrom} duration={duration} />
      <Caption title={title} subtitle={subtitle} />
    </Fade>
  );
};

/* --- AI Agent visual card (no screenshot) --- */
const AgentScene: React.FC<{ duration: number }> = ({ duration }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 200, stiffness: 90 } });
  const cards = [
    { label: "ReAct 推理", color: "#6ea8ff", x: -320 },
    { label: "可插拔技能", color: "#a87bff", x: 0 },
    { label: "工具调用", color: "#5fe0c0", x: 320 },
  ];
  return (
    <Fade duration={duration}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <div style={{ display: "flex", gap: 40, transform: `scale(${interpolate(s,[0,1],[0.85,1])})` }}>
          {cards.map((c, i) => {
            const local = spring({
              frame: frame - i * 6,
              fps,
              config: { damping: 200, stiffness: 90 },
            });
            return (
              <div
                key={c.label}
                style={{
                  width: 280,
                  height: 340,
                  borderRadius: 24,
                  background: "linear-gradient(160deg, rgba(40,50,90,0.9), rgba(20,25,50,0.9))",
                  border: `1px solid ${c.color}66`,
                  boxShadow: `0 20px 60px rgba(0,0,0,0.5), 0 0 40px ${c.color}44`,
                  transform: `translateY(${interpolate(local, [0, 1], [60, 0])}px)`,
                  opacity: local,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <div
                  style={{
                    width: 90,
                    height: 90,
                    borderRadius: 20,
                    background: c.color,
                    opacity: 0.9,
                    boxShadow: `0 0 40px ${c.color}aa`,
                    marginBottom: 24,
                  }}
                />
                <div style={{ fontSize: 30, fontWeight: 600, color: "#e8ecff" }}>{c.label}</div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
      <Caption title="Kevrai Agent" subtitle="ReAct 推理 · 可插拔技能 · 工具调用" />
    </Fade>
  );
};

/* --- Music waveform visual --- */
const MusicScene: React.FC<{ duration: number; variant?: number }> = ({
  duration,
  variant = 0,
}) => {
  const frame = useCurrentFrame();
  const bars = 64;
  return (
    <Fade duration={duration}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, height: 360 }}>
          {Array.from({ length: bars }).map((_, i) => {
            const h =
              60 +
              Math.abs(Math.sin((frame / 8) + i * 0.55 + variant * 1.7) * Math.cos(i * 0.23)) *
                280 *
                (0.6 + 0.4 * Math.sin(i * 0.11));
            const hue = 190 + i * 1.8;
            return (
              <div
                key={i}
                style={{
                  width: 10,
                  height: h,
                  borderRadius: 6,
                  background: `linear-gradient(180deg, hsl(${hue},90%,70%), hsl(${hue + 40},90%,55%))`,
                  boxShadow: `0 0 12px hsla(${hue},90%,65%,0.6)`,
                }}
              />
            );
          })}
        </div>
      </AbsoluteFill>
      <Caption title="MiniMax-Music3" subtitle="AI 音乐创作 · 旋律与伴奏自动生成" />
    </Fade>
  );
};

/* --- Outro --- */
const Outro: React.FC<{
  duration: number;
  buildDate?: string;
  version?: string;
}> = ({ duration, buildDate = "", version = "2.9.0" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 200, stiffness: 70 } });
  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const sloganOpacity = interpolate(frame, [25, 55], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity }}>
      <Img
        src={staticFile("logo.png")}
        style={{
          width: 200,
          height: 200,
          transform: `scale(${interpolate(s, [0, 1], [0.8, 1])})`,
          filter: "drop-shadow(0 0 50px rgba(140,180,255,0.7))",
        }}
      />
      <div
        style={{
          marginTop: 44,
          fontSize: 76,
          fontWeight: 800,
          letterSpacing: 4,
          opacity: sloganOpacity,
          background: "linear-gradient(90deg,#ffffff,#a8c0ff,#d8b8ff)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        }}
      >
        一切 AI，本地运行
      </div>
      <div style={{ marginTop: 20, fontSize: 28, color: "#9fb0d8", letterSpacing: 6, opacity: sloganOpacity }}>
        {`Kevrai Omni · v${version}${buildDate ? ` · ${buildDate}` : ""}`}
      </div>
    </AbsoluteFill>
  );
};

/* ---------------- Main composition ---------------- */

export const KevraiPromo: React.FC<{
  variant?: number;
  buildDate?: string;
  version?: string;
}> = ({ variant = 0, buildDate = "", version = "2.9.0" }) => {
  return (
    <Background variant={variant}>
      <Sequence from={0} durationInFrames={90}>
        <Intro duration={90} />
      </Sequence>

      <Sequence from={90} durationInFrames={120}>
        <ShotScene
          src="screens/01-market.png"
          title="模型市场"
          subtitle="多源测速 · 断点续传 · 一键下载"
          duration={120}
          enterFrom="right"
        />
      </Sequence>

      <Sequence from={210} durationInFrames={120}>
        <ShotScene
          src="screens/02-engines.png"
          title="硬件体检"
          subtitle="NVIDIA / AMD / 昇腾 · 显存自动管理"
          duration={120}
          enterFrom="left"
        />
      </Sequence>

      <Sequence from={330} durationInFrames={120}>
        <AgentScene duration={120} />
      </Sequence>

      <Sequence from={450} durationInFrames={120}>
        <ShotScene
          src="screens/10-generation-wait.png"
          title="短剧工坊"
          subtitle="LTX-2.5 视频生成 · 分镜自动编排"
          duration={120}
          enterFrom="right"
        />
      </Sequence>

      <Sequence from={570} durationInFrames={120}>
        <MusicScene duration={120} variant={variant} />
      </Sequence>

      <Sequence from={690} durationInFrames={120}>
        <ShotScene
          src="screens/03-local.png"
          title="双引擎推理"
          subtitle="MNN + llama.cpp · 本地零云端依赖"
          duration={120}
          enterFrom="left"
        />
      </Sequence>

      <Sequence from={810} durationInFrames={90}>
        <Outro duration={90} buildDate={buildDate} version={version} />
      </Sequence>
    </Background>
  );
};
