#!/usr/bin/env bash
# 从 icon_master.svg 栅格化全套图标。
# icon_master.svg 是唯一事实来源 —— 不要单独编辑任何生成物；改 SVG 后重跑本脚本。
#
# 原子性：所有产物先渲染到同目录下的临时暂存区 .staging.<pid>/，全部成功后再逐个
# `mv -f` 落盘。运行中的 Electron（截图/任务栏图标）只会读到完整的旧文件或完整的
# 新文件，永远不会读到写一半的 PNG/ICO。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SVG="${DIR}/icon_master.svg"
STAGE="${DIR}/.staging.$$"

cleanup() { rm -rf "${STAGE}"; }
trap cleanup EXIT
mkdir -p "${STAGE}"

[ -f "${SVG}" ] || { echo "缺少 ${SVG}"; exit 1; }

# ── 栅格器：优先 rsvg-convert，缺失时回退 cairosvg（二者对本母版输出像素一致） ──
HAVE_RSVG=0
command -v rsvg-convert >/dev/null 2>&1 && HAVE_RSVG=1
if [ "${HAVE_RSVG}" = "1" ]; then
  command -v rsvg-convert >/dev/null || { echo "缺少 rsvg-convert（apt install librsvg2-bin）"; exit 1; }
fi

render_svg() { # $1=边长(px)  $2=输出文件(绝对路径)
  local s="$1" out="$2"
  if [ "${HAVE_RSVG}" = "1" ]; then
    rsvg-convert -w "$s" -h "$s" "${SVG}" -o "${out}"
  else
    python3 - "$s" "$out" "$SVG" <<'PYEOF'
import sys, cairosvg
s = int(sys.argv[1]); out = sys.argv[2]; svg = sys.argv[3]
cairosvg.svg2png(url=svg, output_width=s, output_height=s, write_to=out)
PYEOF
  fi
}

# ── 大尺寸：直接渲染（落到暂存区） ──────────────────────────────────
for s in 1024 512 256 128 64 48 32; do
  render_svg "$s" "${STAGE}/icon-${s}.png"
done

# ── 小尺寸（<=24）：先放大 8 倍再 LANCZOS 缩回，抵消曲线采样损失 ──
for s in 24 16; do
  render_svg $((s * 8)) "${STAGE}/_big_${s}.png"
  python3 - "$s" "$STAGE" <<'PYEOF'
import sys
from PIL import Image, ImageFilter
s = int(sys.argv[1]); out_dir = sys.argv[2]
im = Image.open(f"{out_dir}/_big_{s}.png").convert("RGBA")
im = im.resize((s, s), Image.LANCZOS)
# 轻微锐化，补偿小尺寸下的边缘软化
im = im.filter(ImageFilter.UnsharpMask(radius=0.6, percent=90, threshold=2))
im.save(f"{out_dir}/icon-{s}.png")
PYEOF
done
rm -f "${STAGE}"/_big_*.png

# ── 统一为 RGBA ───────────────────────────────────────────────────
# rsvg/cairo 对不同尺寸输出模式可能不同；格式不一致会让下游（Electron / ICO）行为不统一。
python3 - "$STAGE" <<'PYEOF'
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
python3 - "$STAGE" <<'PYEOF'
import sys
from PIL import Image
out_dir = sys.argv[1]
sizes = [16, 24, 32, 48, 64, 128, 256]
Image.open(f"{out_dir}/icon-256.png").convert("RGBA").save(
    f"{out_dir}/icon.ico", format="ICO", sizes=[(n, n) for n in sizes])
PYEOF

# ── 白底预览（README / 发行说明用） ────────────────────────
python3 - "$STAGE" <<'PYEOF'
import sys
from PIL import Image
out_dir = sys.argv[1]
fg = Image.open(f"{out_dir}/icon-512.png").convert("RGBA")
bg = Image.new("RGBA", fg.size, (255, 255, 255, 255))
bg.alpha_composite(fg)
bg.convert("RGB").save(f"{out_dir}/icon-preview-on-white.png")
PYEOF

# ── 原子发布：暂存区 → 正式位置（逐个 mv，任一文件都是完整的） ──
shopt -s nullglob
# icon-* 已覆盖全部产物（各尺寸 png / icon.ico / icon-preview-on-white.png），
# 不要用 icon-*.png + 显式列表重复枚举，否则预览图会被 mv 两次。
for f in "${STAGE}"/icon-*; do
  base="$(basename "$f")"
  mv -f "$f" "${DIR}/${base}"
done

echo "OK — 栅格化完成（$(ls -1 "${DIR}"/icon-*.png | wc -l) PNG + icon.ico + 预览）"
