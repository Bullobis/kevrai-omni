#!/usr/bin/env bash
# =============================================================================
# scripts/setup/bootstrap.sh — Kevrai Omni 开发者环境引导
#
# 本脚本只做「环境检测 + 可选依赖安装 + 自检」，与 scripts/build_*.sh、
# scripts/release.sh、scripts/upload_release.sh、scripts/smoke.sh 完全独立，
# 不修改它们。
#
# 用法:
#   bash scripts/setup/bootstrap.sh                # 检测 + 装依赖 + 自检
#   bash scripts/setup/bootstrap.sh --dry-run      # 只检测不安装
#   bash scripts/setup/bootstrap.sh --mirror aliyun
#   bash scripts/setup/bootstrap.sh --skip-python-deps
#   bash scripts/setup/bootstrap.sh --skip-npm
#
# 选项:
#   --dry-run              只检测，不执行任何安装
#   --mirror NAME          pip 镜像: tencent(默认) / aliyun / ustc / official
#   --skip-python-deps     不跑 pip install
#   --skip-npm             不跑 npm install
#   -h, --help             打印帮助
# =============================================================================

set -u

# ---------- 颜色 ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_FAIL=$'\033[31m'; C_INFO=$'\033[36m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_FAIL=""; C_INFO=""; C_OFF=""
fi
ok()   { printf "%s[OK]%s   %s\n"   "$C_OK"   "$C_OFF" "$*"; }
warn()  { printf "%s[WARN]%s %s\n"  "$C_WARN" "$C_OFF" "$*"; }
fail()  { printf "%s[FAIL]%s %s\n"  "$C_FAIL" "$C_OFF" "$*"; }
info()  { printf "%s[INFO]%s %s\n"  "$C_INFO" "$C_OFF" "$*"; }

# ---------- 默认参数 ----------
DRY_RUN=0
SKIP_PYTHON_DEPS=0
SKIP_NPM=0
MIRROR="tencent"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-python-deps) SKIP_PYTHON_DEPS=1 ;;
    --skip-npm) SKIP_NPM=1 ;;
    --mirror)
      shift
      MIRROR="${1:-}"
      ;;
    -h|--help) usage 0 ;;
    *) fail "未知参数: $1"; usage 1 ;;
  esac
  shift
done

# 镜像 URL 表
case "$MIRROR" in
  tencent)  PIP_INDEX="https://mirrors.tencent.com/pypi/simple/" ;;
  aliyun)   PIP_INDEX="https://mirrors.aliyun.com/pypi/simple/" ;;
  ustc)     PIP_INDEX="https://pypi.mirrors.ustc.edu.cn/simple/" ;;
  official) PIP_INDEX="https://pypi.org/simple/" ;;
  *) fail "--mirror 只接受 tencent|aliyun|ustc|official, 收到: $MIRROR"; exit 2 ;;
esac

# 切到仓库根（本脚本在 scripts/setup/ 下）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

FAILED=0

echo "=============================================================================="
echo " Kevrai Omni 环境引导  (repo: $REPO_ROOT)"
echo " dry-run=$DRY_RUN  mirror=$MIRROR  skip_python_deps=$SKIP_PYTHON_DEPS  skip_npm=$SKIP_NPM"
echo "=============================================================================="

# ---------- 1. 操作系统检测 ----------
echo
echo "== [1/5] 操作系统 =="
OS_NAME="unknown"
case "$(uname -s)" in
  Linux*)
    OS_NAME="linux"
    if [ -r /etc/os-release ]; then
      . /etc/os-release
      info "Linux: ${PRETTY_NAME:-unknown}"
    else
      info "Linux (无 /etc/os-release)"
    fi
    ;;
  Darwin*)
    OS_NAME="macos"
    info "macOS: $(sw_vers -productVersion 2>/dev/null || echo unknown) ($(uname -m))"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    OS_NAME="windows-gitbash"
    info "Windows (Git Bash / MSYS) — 推荐用 WSL2 或 PowerShell；原生支持有限"
    warn "Windows 上部分 shell 检测可能不准，建议在 Git Bash 或 WSL 中运行"
    ;;
  *)
    OS_NAME="unknown"
    warn "未知系统: $(uname -s)"
    ;;
esac

# ---------- 2. 工具链版本检测 ----------
echo
echo "== [2/5] 工具链 =="

# --- node ---
if command -v node >/dev/null 2>&1; then
  NODE_VER="$(node -p 'process.versions.node' 2>/dev/null || echo '?')"
  NODE_MAJOR="${NODE_VER%%.*}"
  if [ "$NODE_MAJOR" -ge 20 ] 2>/dev/null; then
    ok "node $NODE_VER (>=20)"
  else
    fail "node $NODE_VER 过低 — 需要 >=20 (Electron 33 内置 Node 20.x)"
    FAILED=1
  fi
else
  fail "未找到 node — 请安装 Node.js 20.x 或更高 (https://nodejs.org/)"
  FAILED=1
fi

# --- npm ---
if command -v npm >/dev/null 2>&1; then
  NPM_VER="$(npm -v 2>/dev/null || echo '?')"
  ok "npm $NPM_VER"
else
  fail "未找到 npm (随 Node.js 一起安装)"
  FAILED=1
fi

# --- git ---
if command -v git >/dev/null 2>&1; then
  GIT_VER="$(git --version 2>/dev/null | head -1)"
  ok "$GIT_VER"
else
  fail "未找到 git"
  FAILED=1
fi

# --- python3 ---
PY_BIN=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY_BIN="$cand"
    break
  fi
done
if [ -n "$PY_BIN" ]; then
  PY_VER="$("$PY_BIN" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo '?')"
  PY_MAJOR="${PY_VER%%.*}"
  rest="${PY_VER#*.}"
  PY_MINOR="${rest%%.*}"
  if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 10 ]; }; then
    ok "python $PY_VER ($PY_BIN) — pyproject 要求 >=3.10, CI 用 3.11"
  else
    fail "python $PY_VER 过低 — 需要 >=3.10"
    FAILED=1
  fi
else
  fail "未找到 python3 — 请安装 Python 3.10+ (CI 实测 3.11)"
  FAILED=1
fi

# ---------- 3. 依赖安装（可选） ----------
echo
echo "== [3/5] 依赖安装 =="

if [ "$SKIP_NPM" -eq 1 ]; then
  info "跳过 npm install (--skip-npm)"
elif [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] 将执行: npm install"
else
  if [ -f package.json ]; then
    info "运行 npm install ..."
    if npm install; then
      ok "npm install 完成"
    else
      fail "npm install 失败"
      FAILED=1
    fi
  else
    warn "当前目录无 package.json，跳过 npm install"
  fi
fi

if [ "$SKIP_PYTHON_DEPS" -eq 1 ]; then
  info "跳过 pip install (--skip-python-deps)"
elif [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] 将执行: $PY_BIN -m pip install -i $PIP_INDEX -r python/requirements.txt"
else
  if [ -z "$PY_BIN" ]; then
    fail "没有可用 python，无法装 Python 依赖"
    FAILED=1
  elif [ ! -f python/requirements.txt ]; then
    warn "未找到 python/requirements.txt，跳过"
  else
    info "运行 pip install (-i $PIP_INDEX) ..."
    if "$PY_BIN" -m pip install -i "$PIP_INDEX" -r python/requirements.txt; then
      ok "pip install 完成"
    else
      fail "pip install 失败 (可换镜像: --mirror ustc)"
      FAILED=1
    fi
  fi
fi

# ---------- 4. 自检 ----------
echo
echo "== [4/5] 自检 =="

# 4.1 JS 语法检查（node --check）
JS_FILES="electron/main.js electron/preload.js renderer/app.js renderer/bootstrap.js"
JS_COUNT=0
JS_FAIL=0
for f in $JS_FILES renderer/modules/*.js; do
  [ -f "$f" ] || continue
  JS_COUNT=$((JS_COUNT+1))
  if ! node --check "$f" 2>/dev/null; then
    fail "node --check 失败: $f"
    JS_FAIL=1
  fi
done
if [ "$JS_FAIL" -eq 0 ]; then
  ok "node --check 通过 ($JS_COUNT 个 JS 文件)"
else
  FAILED=1
fi

# 4.2 Python 关键依赖 import
if [ -n "$PY_BIN" ]; then
  IMPORTS_CHECK=$("$PY_BIN" - <<'PY' 2>&1 || true
import importlib, sys
mods = ["fastapi", "uvicorn", "httpx", "pydantic", "huggingface_hub", "yaml", "jsonschema", "websockets", "xxhash"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        missing.append(f"{m}({e.__class__.__name__})")
if missing:
    print("MISSING: " + ", ".join(missing))
    sys.exit(1)
print("OK")
PY
)
  case "$IMPORTS_CHECK" in
    OK) ok "Python 关键依赖可 import (fastapi/uvicorn/httpx/pydantic/huggingface_hub/yaml/jsonschema/websockets/xxhash)" ;;
    *)  fail "Python 依赖缺失: $IMPORTS_CHECK"; FAILED=1 ;;
  esac
else
  warn "无 python，跳过依赖 import 自检"
fi

# 4.3 端口 17890 是否空闲
PORT=17890
PORT_BUSY=0
if command -v lsof >/dev/null 2>&1; then
  if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then PORT_BUSY=1; fi
elif command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then PORT_BUSY=1; fi
elif command -v netstat >/dev/null 2>&1; then
  if netstat -an 2>/dev/null | grep -q "[.:]$PORT .*LISTEN"; then PORT_BUSY=1; fi
else
  warn "找不到 lsof/ss/netstat，跳过端口检测"
fi
if [ "$PORT_BUSY" -eq 1 ]; then
  warn "端口 $PORT 已被占用 — sidecar 启动会冲突；关掉占用方或设 KEVRAI_PORT 换端口"
else
  ok "端口 $PORT 空闲 (sidecar 默认监听)"
fi

# ---------- 5. 下一步提示 ----------
echo
echo "== [5/5] 下一步 =="
if [ "$FAILED" -ne 0 ]; then
  fail "环境检测存在问题，请先修复上面标 [FAIL] 的项。"
else
  ok "基础环境就绪。"
fi
echo
echo "  启动应用:        npm start"
echo "  开发模式:        npm run dev"
echo "  Python 测试:     cd python && python -m pytest -q tests/"
echo "  JS 语法检查:      npm run test:js"
echo "  冒烟测试:        npm run smoke"
echo
echo "  文档: docs/DEPLOYMENT.md · docs/DEVELOPMENT.md · docs/ARCHITECTURE.md"
echo

exit "$FAILED"
