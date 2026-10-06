// renderer/__tests__/dark-win-fixes.test.js
// R9 visual regression: dark-mode contrast + frameless window-control buttons.
//
// These tests read the REAL renderer/styles.css and renderer/index.html from
// disk (no mocked values) so they guard against:
//   1. dark :root dropping a key token (black-on-black / white-on-white),
//   2. the window-control SVG icon collapsing to ~2px (flex item shrink),
//   3. a window button becoming invisible in either theme,
//   4. a theme switch losing variables (no light-theme fallback regressions),
//   5. first-paint FOUC (theme-init not running before stylesheet load).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const stylesPath = path.join(__dirname, "..", "styles.css");
const htmlPath = path.join(__dirname, "..", "index.html");
const css = fs.readFileSync(stylesPath, "utf8");
const html = fs.readFileSync(htmlPath, "utf8");

// ── tiny CSS block / variable parser ──────────────────────────────────────────
function extractBlock(source, selector) {
  // find the selector, then the matching { ... } (handles nested braces at depth 1)
  const idx = source.indexOf(selector);
  if (idx === -1) return null;
  const open = source.indexOf("{", idx);
  if (open === -1) return null;
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(open + 1, i);
    }
  }
  return null;
}
function parseVars(block) {
  const vars = {};
  if (!block) return vars;
  const re = /(--[\w-]+)\s*:\s*([^;]+);/g;
  let m;
  while ((m = re.exec(block))) vars[m[1].trim()] = m[2].trim();
  return vars;
}

const darkVars = parseVars(extractBlock(css, ":root"));
const lightBlock = extractBlock(css, 'html[data-theme="light"]') ||
  extractBlock(css, "html[data-theme='light']") || "";
const lightVars = parseVars(lightBlock);

// ── WCAG contrast helpers ───────────────────────────────────────────────────
function hexToRgb(hex) {
  let h = String(hex).trim();
  if (h.startsWith("#")) h = h.slice(1);
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function luminance([r, g, b]) {
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(rgb1, rgb2) {
  const l1 = luminance(rgb1), l2 = luminance(rgb2);
  const lighter = Math.max(l1, l2), darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

// ── 1. dark :root key tokens exist ──────────────────────────────────────────
test("dark :root defines surface + text + window-control tokens", () => {
  const required = [
    "--stack-0", "--stack-1", "--stack-2", "--stack-3",
    "--fg", "--mut", "--acc", "--card", "--card-hover", "--bord",
    "--win-close", "--bg-0", "--bg-1", "--bg-2",
  ];
  for (const v of required) {
    assert.ok(darkVars[v], `missing dark token ${v}`);
  }
});

// ── 2. window-control buttons present with i18n wiring ────────────────────────
test("index.html ships min/max/close window buttons with i18n aria-labels", () => {
  for (const id of ["win-btn-min", "win-btn-max", "win-btn-close"]) {
    assert.ok(html.includes(`id="${id}"`), `missing button #${id}`);
  }
  for (const key of ["app.winMinimize", "app.winMaximize", "app.winClose", "app.windowControls"]) {
    assert.ok(html.includes(`data-i18n-aria-label="${key}"`), `missing i18n aria ${key}`);
  }
  // win-ctl group container
  assert.ok(html.includes('class="win-ctl"'), "missing .win-ctl group");
});

// ── 3. the flex:none fix prevents SVG icon collapse ───────────────────────────
test(".win-btn .ic pins the icon with flex:none (no 2px collapse)", () => {
  const rule = extractBlock(css, ".win-btn .ic");
  assert.ok(rule, ".win-btn .ic rule missing");
  assert.match(rule, /flex\s*:\s*none/, ".win-btn .ic must set flex:none");
  assert.match(rule, /width\s*:\s*15px/, "icon width should be pinned to 15px");
  assert.match(rule, /height\s*:\s*15px/, "icon height should be pinned to 15px");
});

// ── 4. window button default opacity is readable in both themes ──────────────
test(".win-btn default opacity is >=0.8 (icons visible on dark & light)", () => {
  const rule = extractBlock(css, ".win-btn {") || extractBlock(css, ".win-btn{");
  assert.ok(rule, ".win-btn rule missing");
  const m = rule.match(/opacity\s*:\s*([0-9.]+)/);
  assert.ok(m, "opacity not declared on .win-btn");
  const op = parseFloat(m[1]);
  assert.ok(op >= 0.8, `.win-btn opacity ${op} too low (icons invisible); want >=0.8`);
});

// ── 5. key dark text pairs meet WCAG AA (>=4.5:1) ───────────────────────────
test("dark theme key text/background pairs meet WCAG AA (>=4.5:1)", () => {
  const fg = hexToRgb(darkVars["--fg"]);
  const mut = hexToRgb(darkVars["--mut"]);
  const stack0 = hexToRgb(darkVars["--stack-0"]);
  const stack1 = hexToRgb(darkVars["--stack-1"]);
  assert.ok(fg && mut && stack0 && stack1, "could not parse dark hex tokens");
  const pairs = [
    ["--fg on --stack-0 (app bg)", fg, stack0],
    ["--fg on --stack-1 (panel)", fg, stack1],
    ["--mut on --stack-0 (muted)", mut, stack0],
    ["--mut on --stack-1 (muted panel)", mut, stack1],
  ];
  for (const [label, a, b] of pairs) {
    const r = contrast(a, b);
    assert.ok(r >= 4.5, `${label} contrast ${r.toFixed(2)}:1 < 4.5:1`);
  }
});

// ── 6. light theme overrides the same key tokens (no missing-var regressions) ─
test("light theme overrides the same key tokens (no black-on-black fallback)", () => {
  for (const v of ["--fg", "--mut", "--stack-0", "--stack-1", "--acc"]) {
    assert.ok(lightVars[v], `light theme missing override ${v}`);
  }
  // light fg must be dark-ish, dark fg must be light-ish (inverted)
  const darkFgLum = luminance(hexToRgb(darkVars["--fg"]));
  const lightFgLum = luminance(hexToRgb(lightVars["--fg"]));
  assert.ok(lightFgLum < darkFgLum, "light --fg should be darker than dark --fg");
  const lightFg = hexToRgb(lightVars["--fg"]);
  const lightBg = hexToRgb(lightVars["--stack-0"]);
  const r = contrast(lightFg, lightBg);
  assert.ok(r >= 4.5, `light --fg on --stack-0 contrast ${r.toFixed(2)}:1 < 4.5:1`);
});

// ── 7. close-button hover uses the shared danger token in both themes ────────
test("--win-close token defined once for both-theme close-button hover", () => {
  assert.ok(darkVars["--win-close"], ":root must define --win-close");
  const closeRule = extractBlock(css, ".win-btn-close:hover");
  assert.ok(closeRule, ".win-btn-close:hover rule missing");
  assert.match(closeRule, /--win-close/, "close hover must reference --win-close");
});

// ── 8. FOUC guard: theme-init runs synchronously before the stylesheet ───────
test("theme-init.js loads synchronously in <head> before styles.css (no FOUC)", () => {
  const initIdx = html.indexOf('src="theme-init.js"');
  const cssIdx = html.indexOf('href="styles.css"');
  assert.ok(initIdx !== -1, "theme-init.js not linked in index.html");
  assert.ok(cssIdx !== -1, "styles.css not linked in index.html");
  assert.ok(initIdx < cssIdx, "theme-init must run before styles.css to avoid first-paint flash");
});
