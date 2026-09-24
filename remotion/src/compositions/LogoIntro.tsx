import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { GradientBg } from "../components/GradientBg";
import { ParticleField } from "../components/ParticleField";
import { TypeWriter } from "../components/TypeWriter";

export type LogoIntroProps = {
  version: string;
  date: string;
};

const TAGS = ["LLM", "TTS", "Image", "Video", "3D", "Agent"];
const TAG_START = 150; // 第 150 帧开始滑入标签
const TAG_STAGGER = 15; // 每个标签间隔 15 帧

export const LogoIntro: React.FC<LogoIntroProps> = ({ version, date }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();

  // --- 阶段 2 (60-150): logo 缩放入场 + 光晕脉动 ---
  const logoScale = interpolate(frame, [60, 110], [0.8, 1.0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const logoOpacity = interpolate(frame, [55, 80], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // 光晕脉动: 60-240 帧之间循环
  const pulse =
    0.5 +
    0.5 *
      Math.sin(
        ((frame - 60) / 30) * Math.PI * 2 * 1.5
      );
  const glowRadius = interpolate(pulse, [0, 1], [20, 60]);
  const glowAlpha = interpolate(pulse, [0, 1], [0.3, 0.8]);

  // --- 阶段 4 (240-300): 版本号 + 日期淡入 ---
  const metaOpacity = interpolate(frame, [240, 270], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // --- 整体渐隐 (280-300) ---
  const globalFade = interpolate(frame, [280, 300], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ opacity: globalFade }}>
      <GradientBg />
      {/* 粒子汇聚 0-60 帧 */}
      <ParticleField
        width={width}
        height={height}
        particleCount={140}
        convergeFrames={60}
      />

      {/* 中央内容 */}
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            transform: `scale(${logoScale})`,
            opacity: logoOpacity,
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 140,
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.02em",
              textShadow: `0 0 ${glowRadius}px rgba(76,169,122,${glowAlpha}), 0 0 ${
                glowRadius * 2
              }px rgba(76,169,122,${glowAlpha * 0.5})`,
              fontFamily: "sans-serif",
            }}
          >
            Kevrai <span style={{ color: "#4ca97a" }}>Omni</span>
          </div>
          <TypeWriter
            text="Local AI Workstation"
            startFrame={75}
            cps={10}
            fps={30}
            style={{
              marginTop: 24,
              fontSize: 36,
              color: "#9aa0aa",
              letterSpacing: "0.1em",
            }}
          />
        </div>

        {/* 功能标签 */}
        <div
          style={{
            marginTop: 80,
            display: "flex",
            gap: 20,
            flexWrap: "wrap",
            justifyContent: "center",
          }}
        >
          {TAGS.map((tag, i) => {
            const start = TAG_START + i * TAG_STAGGER;
            const y = interpolate(frame, [start, start + 15], [40, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const op = interpolate(frame, [start, start + 10], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return (
              <div
                key={tag}
                style={{
                  transform: `translateY(${y}px)`,
                  opacity: op,
                  padding: "10px 22px",
                  borderRadius: 999,
                  border: "1px solid rgba(76,169,122,0.5)",
                  backgroundColor: "rgba(76,169,122,0.12)",
                  color: "#cfe9db",
                  fontSize: 26,
                  fontWeight: 600,
                  fontFamily: "sans-serif",
                }}
              >
                {tag}
              </div>
            );
          })}
        </div>
      </AbsoluteFill>

      {/* 底部版本号 + 日期 */}
      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          alignItems: "center",
          paddingBottom: 60,
          opacity: metaOpacity,
        }}
      >
        <div
          style={{
            fontSize: 28,
            color: "#6b7280",
            fontFamily: "sans-serif",
            letterSpacing: "0.05em",
          }}
        >
          {version} &nbsp;·&nbsp; {date}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
