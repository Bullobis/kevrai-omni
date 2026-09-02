"use strict";
/**
 * Creative generation-wait overlay for image/video generation tasks.
 *
 * Visual language (matching the user's reference):
 * - fluid cyan/teal/blue gradient background
 * - floating soft-particle field
 * - pulsing KO logo at center
 * - cycling stage captions + optional progress bar
 */

const NS = "http://www.w3.org/2000/svg";
let activeInstance = null;

function formatBytes(b) {
  if (b == null || b < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function buildOverlay(options) {
  const opts = Object.assign({
    title: "正在生成",
    captions: ["构思中…", "调用引擎…", "采样像素…", "优化细节…", "即将完成…"],
    showProgress: true,
    showCancel: true,
    indeterminate: false,
    onCancel: null,
  }, options);

  const root = document.createElement("div");
  root.id = "generation-wait-overlay";
  root.className = "generation-wait-overlay";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-live", "polite");
  root.setAttribute("aria-label", opts.title);

  root.innerHTML = `
    <div class="gw-fluid">
      <div class="gw-blob gw-blob-a"></div>
      <div class="gw-blob gw-blob-b"></div>
      <div class="gw-blob gw-blob-c"></div>
    </div>
    <canvas class="gw-particles" aria-hidden="true"></canvas>
    <div class="gw-content">
      <div class="gw-logo-ring">
        <img class="gw-logo" src="../assets/icons/icon-256.png" width="96" height="96" alt="Kevrai Omni" id="gw-logo-img" />
      </div>
      <div class="gw-title">${escapeHtml(opts.title)}</div>
      <div class="gw-caption" id="gw-caption">${escapeHtml(opts.captions[0])}</div>
      ${opts.showProgress ? `
        <div class="gw-progress-wrap">
          <div class="gw-progress-bar" id="gw-progress-bar" style="width:0%"></div>
        </div>
        <div class="gw-progress-meta">
          <span id="gw-percent">0%</span>
          <span id="gw-meta"></span>
        </div>
      ` : ""}
      ${opts.showCancel ? `<button class="gw-cancel" id="gw-cancel" type="button">取消</button>` : ""}
    </div>
  `;

  document.body.appendChild(root);

  // Particle canvas setup
  const canvas = root.querySelector(".gw-particles");
  const ctx = canvas.getContext("2d");
  let width = 0, height = 0, particles = [], rafId = null, running = true;

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width;
    canvas.height = height;
  }
  resize();
  window.addEventListener("resize", resize, { passive: true });

  const particleCount = Math.min(80, Math.floor((width * height) / 18000));
  for (let i = 0; i < particleCount; i++) {
    particles.push({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4 - 0.15,
      r: Math.random() * 2 + 1,
      a: Math.random() * 0.5 + 0.2,
    });
  }

  function drawParticles() {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    for (const p of particles) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.globalAlpha = p.a;
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    // neighbor lines
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 1;
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 120 * 120) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.globalAlpha = 1 - Math.sqrt(d2) / 120;
          ctx.stroke();
        }
      }
    }
    ctx.globalAlpha = 1;
  }

  function updateParticles() {
    for (const p of particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -10) p.x = width + 10;
      if (p.x > width + 10) p.x = -10;
      if (p.y < -10) p.y = height + 10;
      if (p.y > height + 10) p.y = -10;
    }
  }

  function loop() {
    if (!running) return;
    updateParticles();
    drawParticles();
    rafId = requestAnimationFrame(loop);
  }
  loop();

  // Caption cycling
  const captionEl = root.querySelector("#gw-caption");
  let captionIdx = 0;
  const captionTimer = setInterval(() => {
    if (!captionEl) return;
    captionIdx = (captionIdx + 1) % opts.captions.length;
    captionEl.textContent = opts.captions[captionIdx];
  }, 2200);

  // Logo fallback: if the icon PNG fails to load, show a text glyph.
  const logoImg = root.querySelector("#gw-logo-img");
  if (logoImg) {
    logoImg.addEventListener("error", () => {
      logoImg.style.display = "none";
      const fallback = document.createElement("div");
      fallback.className = "gw-logo-fallback";
      fallback.textContent = "◈";
      fallback.setAttribute("aria-hidden", "true");
      logoImg.parentNode.appendChild(fallback);
    }, { once: true });
  }

  // Cancel
  if (opts.showCancel && opts.onCancel) {
    root.querySelector("#gw-cancel").addEventListener("click", () => {
      opts.onCancel();
      hideGenerationWait();
    });
  }

  const api = {
    element: root,
    setProgress(percent, meta) {
      const pct = Math.max(0, Math.min(100, Math.round(percent)));
      const bar = root.querySelector("#gw-progress-bar");
      const pctEl = root.querySelector("#gw-percent");
      const metaEl = root.querySelector("#gw-meta");
      if (bar) bar.style.width = `${pct}%`;
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (metaEl && meta != null) metaEl.textContent = meta;
    },
    setCaption(text) {
      const el = root.querySelector("#gw-caption");
      if (el) el.textContent = text;
    },
    destroy() {
      running = false;
      if (rafId) cancelAnimationFrame(rafId);
      clearInterval(captionTimer);
      window.removeEventListener("resize", resize);
      if (root.parentNode) root.parentNode.removeChild(root);
      if (activeInstance === api) activeInstance = null;
    },
  };

  activeInstance = api;
  return api;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function showGenerationWait(options) {
  if (activeInstance) activeInstance.destroy();
  return buildOverlay(options);
}

export function hideGenerationWait() {
  if (activeInstance) activeInstance.destroy();
}

export function getActiveGenerationWait() {
  return activeInstance;
}
