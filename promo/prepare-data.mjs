// Generates src/data.json from the real catalog so the promo always reflects
// the shipped model/engine counts. The "featured" selection is rotated by the
// calendar day, which is what makes each scheduled render genuinely different.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

const modelsDoc = JSON.parse(
  fs.readFileSync(path.join(root, "catalog/models.json"), "utf-8"),
);
const enginesDoc = JSON.parse(
  fs.readFileSync(path.join(root, "catalog/engines.json"), "utf-8"),
);
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf-8"));

const tz = "Asia/Shanghai";
const now = new Date();
const dateParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: tz,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).formatToParts(now);
const get = (t) => dateParts.find((p) => p.type === t).value;
const dateLabel = `${get("year")}-${get("month")}-${get("day")}`;
// Deterministic per-day rotation seed.
const dayIndex = Math.floor(now.getTime() / 86_400_000);

const CATEGORY_LABELS = {
  llm: "LLM 大模型",
  tts: "语音 TTS",
  video: "视频生成",
  image: "图像生成",
  superres: "超分辨率",
  audio: "音频 / 音乐",
  "3d": "3D 生成",
  vision: "视觉工具",
  pending: "待官方开源",
};

const allModels = modelsDoc.models;
const allEngines = enginesDoc.engines;

const categoryCounts = {};
for (const m of allModels) {
  categoryCounts[m.category] = (categoryCounts[m.category] || 0) + 1;
}

const rotate = (arr, n) => {
  if (!arr.length) return arr;
  const k = ((n % arr.length) + arr.length) % arr.length;
  return arr.slice(k).concat(arr.slice(0, k));
};

// Build a rotated, category-balanced featured list for the market scene.
const byCategory = {};
for (const m of allModels) {
  if (m.category === "pending" || !m.trending) continue;
  (byCategory[m.category] ||= []).push(m);
}
const cats = Object.keys(byCategory);
const featured = [];
const FEATURED_N = 8;
let guard = 0;
while (featured.length < FEATURED_N && guard < 50) {
  for (const c of cats) {
    const list = rotate(byCategory[c], dayIndex + guard);
    const pick = list[guard % list.length];
    if (pick && !featured.includes(pick)) {
      featured.push(pick);
      if (featured.length >= FEATURED_N) break;
    }
  }
  guard++;
}

const slim = (m) => ({
  name: m.name,
  category: m.category,
  categoryLabel: CATEGORY_LABELS[m.category] || m.category,
  size: typeof m.size_gb === "number" ? m.size_gb : null,
  license: m.license || "",
  engine: Array.isArray(m.engine) ? m.engine.slice(0, 2) : [],
  description: m.description || "",
});

// A few representative engines to show in the engine scene.
const engineNames = allEngines.filter((e) => e.trending).slice(0, 10).map((e) => e.name);

const activeCategories = Object.keys(categoryCounts).filter((c) => c !== "pending");

const data = {
  product: pkg.productName || pkg.name,
  version: pkg.version,
  dateLabel,
  dayIndex,
  totals: {
    models: allModels.length,
    engines: allEngines.length,
    categories: activeCategories.length,
  },
  categoryCounts,
  categoryLabels: CATEGORY_LABELS,
  featured: featured.map(slim),
  engines: engineNames,
  stats: [
    { value: allModels.length, label: "开源模型" },
    { value: allEngines.length, label: "AI 引擎" },
    { value: activeCategories.length, label: "内容大类" },
    { value: 0, label: "云端依赖 · 全本地运行" },
  ],
  features: [
    { icon: "◇", title: "模型市场", desc: "多源测速 · 断点续传" },
    { icon: "⚡", title: "硬件体检", desc: "NVIDIA · AMD ROCm · 昇腾" },
    { icon: "🤖", title: "Kevrai Agent", desc: "ReAct 推理 · 技能库" },
    { icon: "🎥", title: "LTX-2.5 视频", desc: "文生视频 · 图生视频" },
    { icon: "🎵", title: "Music3 音乐", desc: "立体声 · 最长 5 分钟" },
    { icon: "⬢", title: "双引擎", desc: "llama.cpp · MNN" },
  ],
  agentLines: [
    "检查本地硬件与显存…",
    "Action · check_hardware",
    "Observation · GPU 24GB VRAM",
    "推荐 LTX-2.5「高质量」预设 ✓",
  ],
  repo: "github.com/Bullobis/kevrai-omni",
};

fs.mkdirSync(path.join(here, "src"), { recursive: true });
fs.writeFileSync(
  path.join(here, "src/data.json"),
  JSON.stringify(data, null, 2) + "\n",
);
console.log(
  `data.json written · ${data.totals.models} models · ${data.totals.engines} engines · ${dateLabel}`,
);
