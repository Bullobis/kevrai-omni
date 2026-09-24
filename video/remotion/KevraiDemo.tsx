import React from "react";
import {
  AbsoluteFill,
  interpolate,
  Sequence,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type DemoProps = {
  generatedAt: string;
  modelCount: number;
  engineCount: number;
};

const colors = {
  bg: "#0d0e11",
  panel: "#131418",
  panel2: "#1a1c21",
  text: "#e8eaed",
  muted: "#9aa0a6",
  accent: "#4ca97a",
  border: "rgba(255,255,255,0.09)",
};

const fontStack = 'system-ui, -apple-system, "Segoe UI", sans-serif';

const capabilities = [
  "LLM",
  "TTS",
  "Image",
  "Video",
  "3D",
  "Audio",
  "Super-Res",
  "MNN",
];

const workflow = ["Install engine", "Choose model", "Generate locally"];

const Chip: React.FC<{children: React.ReactNode; delay: number}> = ({
  children,
  delay,
}) => {
  const frame = useCurrentFrame();
  const local = frame - delay;
  const opacity = interpolate(local, [0, 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const translateY = interpolate(local, [0, 12], [14, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        border: `1px solid ${colors.border}`,
        background: colors.panel2,
        color: colors.text,
        borderRadius: 999,
        padding: "12px 20px",
        fontSize: 24,
        fontWeight: 500,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      {children}
    </div>
  );
};

const BrandScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrance = spring({frame, fps, config: {damping: 18, stiffness: 110}});
  const logoScale = interpolate(entrance, [0, 1], [0.72, 1]);
  const titleY = interpolate(entrance, [0, 1], [24, 0]);

  return (
    <AbsoluteFill
      style={{
        background: colors.bg,
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        padding: 60,
      }}
    >
      <div
        style={{
          width: 92,
          height: 92,
          borderRadius: 24,
          border: `1px solid ${colors.border}`,
          background: colors.panel,
          color: colors.accent,
          fontSize: 54,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transform: `scale(${logoScale})`,
          marginBottom: 28,
        }}
      >
        ◈
      </div>
      <div
        style={{
          color: colors.accent,
          fontSize: 20,
          letterSpacing: 3,
          textTransform: "uppercase",
          marginBottom: 14,
        }}
      >
        Local AI workstation
      </div>
      <h1
        style={{
          color: colors.text,
          fontSize: 76,
          lineHeight: 1,
          margin: 0,
          transform: `translateY(${titleY}px)`,
        }}
      >
        Kevrai Omni
      </h1>
      <p style={{color: colors.muted, fontSize: 28, marginTop: 22}}>
        Engines, models, and media generation in one local workflow
      </p>
    </AbsoluteFill>
  );
};

const CapabilitiesScene: React.FC<DemoProps> = ({
  generatedAt,
  modelCount,
  engineCount,
}) => {
  const frame = useCurrentFrame();
  const headingOpacity = interpolate(frame, [0, 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        background: colors.bg,
        padding: 78,
        justifyContent: "center",
      }}
    >
      <h2
        style={{
          color: colors.text,
          fontSize: 52,
          margin: 0,
          opacity: headingOpacity,
        }}
      >
        One workstation, many modalities
      </h2>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 14,
          marginTop: 38,
          maxWidth: 1020,
        }}
      >
        {capabilities.map((item, index) => (
          <Chip key={item} delay={18 + index * 7}>
            {item}
          </Chip>
        ))}
      </div>
      <div
        style={{
          position: "absolute",
          left: 78,
          bottom: 58,
          right: 78,
          color: colors.muted,
          fontSize: 22,
        }}
      >
        {modelCount} catalog models · {engineCount} engines · {generatedAt}
      </div>
    </AbsoluteFill>
  );
};

const WorkflowScene: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill
      style={{background: colors.bg, padding: 76, justifyContent: "center"}}
    >
      <h2 style={{color: colors.text, fontSize: 52, margin: "0 0 38px"}}>
        From setup to generation
      </h2>
      <div style={{display: "flex", gap: 22}}>
        {workflow.map((item, index) => {
          const delay = 10 + index * 18;
          const local = frame - delay;
          const opacity = interpolate(local, [0, 16], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const scale = interpolate(local, [0, 16], [0.94, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const progress = interpolate(local, [18, 48], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });

          return (
            <div
              key={item}
              style={{
                flex: 1,
                minHeight: 230,
                border: `1px solid ${colors.border}`,
                background: colors.panel,
                borderRadius: 22,
                padding: 26,
                opacity,
                transform: `scale(${scale})`,
              }}
            >
              <div
                style={{
                  width: 42,
                  height: 42,
                  borderRadius: 12,
                  background: colors.accent,
                  color: "#08110d",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 22,
                  fontWeight: 700,
                  marginBottom: 24,
                }}
              >
                {index + 1}
              </div>
              <div style={{color: colors.text, fontSize: 30, fontWeight: 600}}>
                {item}
              </div>
              <div
                style={{
                  height: 8,
                  borderRadius: 999,
                  background: colors.panel2,
                  marginTop: 34,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${progress * 100}%`,
                    height: "100%",
                    background: colors.accent,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
      <div
        style={{
          marginTop: 44,
          color: colors.accent,
          fontSize: 30,
          fontWeight: 600,
        }}
      >
        Local first · Open source · Hardware aware
      </div>
    </AbsoluteFill>
  );
};

export const KevraiDemo: React.FC<DemoProps> = (props) => {
  const frame = useCurrentFrame();
  const scene1 = interpolate(frame, [0, 14, 56, 70], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scene2 = interpolate(frame, [60, 74, 126, 140], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scene3 = interpolate(frame, [130, 144, 196, 210], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{background: colors.bg, fontFamily: fontStack}}>
      <Sequence from={0} durationInFrames={70}>
        <AbsoluteFill style={{opacity: scene1, pointerEvents: "none"}}>
          <BrandScene />
        </AbsoluteFill>
      </Sequence>
      <Sequence from={60} durationInFrames={80}>
        <AbsoluteFill style={{opacity: scene2, pointerEvents: "none"}}>
          <CapabilitiesScene {...props} />
        </AbsoluteFill>
      </Sequence>
      <Sequence from={130} durationInFrames={80}>
        <AbsoluteFill style={{opacity: scene3, pointerEvents: "none"}}>
          <WorkflowScene />
        </AbsoluteFill>
      </Sequence>
    </AbsoluteFill>
  );
};
