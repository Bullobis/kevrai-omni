import React from "react";

// Vector redraw of the app icon (indigo→blue rounded square, ring, K, dot).
export const Logo = ({ size = 160 }) => {
  const t = Math.max(2, size * 0.03);
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.28,
        background: "linear-gradient(140deg,#8b5cf6 0%,#6366f1 46%,#38bdf8 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        boxShadow: "0 14px 44px rgba(99,102,241,0.38)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: size * 0.64,
          height: size * 0.64,
          borderRadius: "50%",
          border: `${t}px solid rgba(255,255,255,0.32)`,
          borderTopColor: "rgba(103,232,249,0.95)",
          transform: "rotate(-28deg)",
        }}
      />
      <span
        style={{
          color: "#fff",
          fontWeight: 800,
          fontSize: size * 0.6,
          lineHeight: 1,
          marginLeft: size * 0.03,
          marginTop: size * 0.02,
        }}
      >
        K
      </span>
      <span
        style={{
          position: "absolute",
          left: "47%",
          top: "58%",
          width: size * 0.11,
          height: size * 0.11,
          borderRadius: "50%",
          background: "#fff",
          border: `${size * 0.028}px solid #67e8f9`,
          transform: "translate(-50%,-50%)",
        }}
      />
    </div>
  );
};
