import React from "react";
import { theme } from "../theme.js";

// Desktop window chrome (title bar with dots), reused across scenes.
export const WindowFrame = ({ children, title = "Kevrai Omni", width, height, style }) => (
  <div
    style={{
      width,
      height,
      borderRadius: 16,
      overflow: "hidden",
      background: theme.panel,
      border: `1px solid ${theme.line}`,
      boxShadow: "0 34px 90px rgba(0,0,0,0.55)",
      display: "flex",
      flexDirection: "column",
      ...style,
    }}
  >
    <div
      style={{
        height: 48,
        background: theme.card,
        borderBottom: `1px solid ${theme.line}`,
        display: "flex",
        alignItems: "center",
        padding: "0 18px",
        gap: 9,
        flexShrink: 0,
      }}
    >
      <Dot color="#ff5f57" />
      <Dot color="#febc2e" />
      <Dot color="#28c840" />
      <span style={{ flex: 1, textAlign: "center", color: theme.mut, fontSize: 16, fontWeight: 500 }}>
        {title}
      </span>
      <span style={{ width: 52 }} />
    </div>
    {children}
  </div>
);

const Dot = ({ color }) => (
  <span style={{ width: 13, height: 13, borderRadius: "50%", background: color, display: "inline-block" }} />
);
