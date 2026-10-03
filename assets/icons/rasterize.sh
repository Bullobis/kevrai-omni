#!/usr/bin/env bash
# 从 icon_master.svg 栅格化全套图标。
# icon_master.svg 是唯一事实来源 —— 不要单独编辑任何生成物；改 SVG 后重跑本脚本。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SVG="${DIR}/icon_master.svg"

command -v rsvg-convert >/dev/null || { echo "缺少 rsvg-convert（apt install librsvg2-bin）"; exit 1; }
[ -f "${SVG}" ] || { echo "缺少 ${SVG}"; exit 1; }

# ── 大尺寸：直接渲染 ────────────────────────────────────────────────
for s in 1024 512 256 128 64 48 32; do
  rsvg-convert -w "$s" -h "$s" "${SVG}" -o "${DIR}/icon-${s}.png"
done

# ── 小尺寸（<=24）：先放大 8 倍再 LANCZOS 缩回，抵消曲线采样损失 ──
for s in 24 16; do
  rsvg-convert -w $((s * 8)) -h $((s * 8)) "${SVG}" -o "/tmp/_big_${s}.png"
  python3 - "$s" "$DIR" <<'PYEOF'
import sys
from PIL import Image, ImageFilter
s = int(sys.argv[1]); out_dir = sys.argv[2]
im = Image.open(f"/tmp/_big_{s}.png").convert("RGBA")
im = im.resize((s, s), Image.LANCZOS)
# 轻微锐化，补偿小尺寸下的边缘软化
im = im.filter(ImageFilter.UnsharpMask(radius=0.6, percent=90, threshold=2))
im.save(f"{out_dir}/icon-{s}.png")
PYEOF
done

# ── 统一为 RGBA ───────────────────────────────────────────────────
# rsvg 对 >=32px 输出 RGB；格式不一致会让下游（Electron / ICO）行为不统一。
python3 - "$DIR" <<'PYEOF'
import sys, glob
from PIL import Image
for p in sorted(glob.glob(f"{sys.argv[1]}/icon-*.png")):
    im = Image.open(p)
    if im.mode != "RGBA":
        im.convert("RGBA").save(p)
PYEOF

# ── ICO ───────────────────────────────────────────────────────────
# 尺寸集合必须与旧版一致：16 / 24 / 32 / 48 / 64 / 128 / 256。
# PIL 的 ICO 插件只认「基准图 + sizes」；append_images 不生效，
# 且基准图必须用最大那张，否则只写一层。
python3 - "$DIR" <<'PYEOF'
import sys
from PIL import Image
out_dir = sys.argv[1]
sizes = [16, 24, 32, 48, 64, 128, 256]
Image.open(f"{out_dir}/icon-256.png").convert("RGBA").save(
    f"{out_dir}/icon.ico", format="ICO", sizes=[(n, n) for n in sizes])
PYEOF

# ── 白底预览（README / 发行说明用） ────────────────────────────────
python3 - "$DIR" <<'PYEOF'
import sys
from PIL import Image
out_dir = sys.argv[1]
fg = Image.open(f"{out_dir}/icon-512.png").convert("RGBA")
bg = Image.new("RGBA", fg.size, (255, 255, 255, 255))
bg.alpha_composite(fg)
bg.convert("RGB").save(f"{out_dir}/icon-preview-on-white.png")
PYEOF

echo "OK — 栅格化完成"
