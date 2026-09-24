import React from "react";

// 深色渐变背景，与 Kevrai 应用主题一致 (#0d0e11 -> #1a1c21)
export const GradientBg: React.FC<{ children?: React.ReactNode }> = ({
  children,
}) => {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background:
          "radial-gradient(ellipse at center, #1a1c21 0%, #0d0e11 100%)",
      }}
    >
      {children}
    </div>
  );
};
