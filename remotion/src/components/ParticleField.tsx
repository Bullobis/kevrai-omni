import React, { useMemo } from "react";
import { interpolate, useCurrentFrame } from "remotion";

// 简单的 seeded PRNG (mulberry32)，保证渲染帧之间粒子位置可复现
const mulberry32 = (seed: number) => {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

type Particle = {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  size: number;
  delay: number;
  hue: number;
};

export const ParticleField: React.FC<{
  width: number;
  height: number;
  particleCount?: number;
  convergeFrames?: number; // 汇聚所需帧数
  holdOpacity?: number; // 汇聚完成后粒子保留的透明度
}> = ({
  width,
  height,
  particleCount = 140,
  convergeFrames = 60,
  holdOpacity = 0.9,
}) => {
  const frame = useCurrentFrame();

  const particles = useMemo<Particle[]>(() => {
    const rand = mulberry32(42);
    const list: Particle[] = [];
    const cx = width / 2;
    const cy = height / 2;
    for (let i = 0; i < particleCount; i++) {
      // 起点：从屏幕四周随机位置进入
      const edge = Math.floor(rand() * 4);
      let startX = 0;
      let startY = 0;
      if (edge === 0) {
        startX = rand() * width;
        startY = -20;
      } else if (edge === 1) {
        startX = width + 20;
        startY = rand() * height;
      } else if (edge === 2) {
        startX = rand() * width;
        startY = height + 20;
      } else {
        startX = -20;
        startY = rand() * height;
      }
      // 终点：围绕中心的椭圆区域（logo 周围）
      const angle = rand() * Math.PI * 2;
      const radius = 60 + rand() * 260;
      const endX = cx + Math.cos(angle) * radius * 1.8;
      const endY = cy + Math.sin(angle) * radius * 0.9;
      list.push({
        startX,
        startY,
        endX,
        endY,
        size: 2 + rand() * 4,
        delay: Math.floor(rand() * 12),
        hue: 140 + rand() * 30, // 绿色系
      });
    }
    return list;
  }, [width, height, particleCount]);

  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {particles.map((p, i) => {
        const t = interpolate(frame - p.delay, [0, convergeFrames], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const x = p.startX + (p.endX - p.startX) * t;
        const y = p.startY + (p.endY - p.startY) * t;
        const opacity = interpolate(t, [0, 0.1, 1], [0, holdOpacity, holdOpacity]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: p.size,
              height: p.size,
              borderRadius: p.size,
              backgroundColor: `hsl(${p.hue}, 60%, 65%)`,
              opacity,
              boxShadow: `0 0 ${p.size * 2}px hsla(${p.hue}, 60%, 65%, 0.8)`,
            }}
          />
        );
      })}
    </div>
  );
};
