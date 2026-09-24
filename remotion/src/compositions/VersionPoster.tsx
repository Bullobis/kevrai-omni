import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { GradientBg } from "../components/GradientBg";

export type VersionPosterProps = {
  version: string;
  date: string;
  highlights: string[];
};

const HIGHLIGHT_STAGGER = 70; // 每条亮点间隔帧数
const HIGHLIGHT_START = 90;

export const VersionPoster: React.FC<VersionPosterProps> = ({
  version,
  date,
  highlights,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // 版本号从上方落入 (spring)
  const drop = spring({
    frame,
    fps,
    from: -600,
    to: 0,
    config: { damping: 12, stiffness: 90 },
  });
  const titleY = interpolate(drop, [0, 1], [-300, 0]);
  const titleOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateRight: "clamp",
  });

  // 按钮脉冲
  const pulse =
    1 + 0.06 * Math.sin(((frame - 150) / 30) * Math.PI * 2 * 1.2);
  const buttonOpacity = interpolate(frame, [150, 180], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <GradientBg />
      <AbsoluteFill
        style={{
          flexDirection: "column",
          alignItems: "center",
          padding: 80,
          justifyContent: "space-between",
        }}
      >
        {/* 顶部小标签 */}
        <div
          style={{
            fontSize: 28,
            color: "#4ca97a",
            letterSpacing: "0.3em",
            fontFamily: "sans-serif",
            fontWeight: 600,
            opacity: titleOpacity,
          }}
        >
          KEVRAI OMNI
        </div>

        {/* 版本大标题 */}
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            opacity: titleOpacity,
            fontSize: 180,
            fontWeight: 900,
            color: "#ffffff",
            fontFamily: "sans-serif",
            textShadow: "0 0 60px rgba(76,169,122,0.4)",
            textAlign: "center",
          }}
        >
          {version}
        </div>

        {/* 更新亮点列表 */}
        <div style={{ flex: 1, width: "100%", marginTop: 40 }}>
          {highlights.map((h, i) => {
            const start = HIGHLIGHT_START + i * HIGHLIGHT_STAGGER;
            const x = interpolate(frame, [start, start + 20], [-800, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const op = interpolate(frame, [start, start + 15], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return (
              <div
                key={i}
                style={{
                  transform: `translateX(${x}px)`,
                  opacity: op,
                  display: "flex",
                  alignItems: "center",
                  gap: 20,
                  marginBottom: 24,
                }}
              >
                <div
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: 6,
                    backgroundColor: "#4ca97a",
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    fontSize: 36,
                    color: "#e5e7eb",
                    fontFamily: "sans-serif",
                    fontWeight: 500,
                  }}
                >
                  {h}
                </div>
              </div>
            );
          })}
        </div>

        {/* 下载按钮 */}
        <div
          style={{
            transform: `scale(${pulse})`,
            opacity: buttonOpacity,
            padding: "24px 64px",
            borderRadius: 999,
            background: "linear-gradient(135deg, #4ca97a, #3a8a63)",
            color: "#ffffff",
            fontSize: 40,
            fontWeight: 700,
            fontFamily: "sans-serif",
            boxShadow: "0 10px 40px rgba(76,169,122,0.4)",
          }}
        >
          Download now
        </div>

        {/* 日期 */}
        <div
          style={{
            marginTop: 24,
            fontSize: 22,
            color: "#6b7280",
            fontFamily: "sans-serif",
            opacity: buttonOpacity,
          }}
        >
          {date}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
