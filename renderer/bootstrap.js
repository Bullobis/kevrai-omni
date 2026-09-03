// renderer/bootstrap.js — first-run environment bootstrap page (v2.5.0).
"use strict";

const $ = (s) => document.querySelector(s);

function setBusy(busy) {
  document.querySelectorAll(".btn").forEach((b) => (b.disabled = busy));
}

function appendLog(text) {
  const log = $("#bs-log");
  if (!text) return;
  log.hidden = false;
  log.textContent = (log.textContent + "\n" + text).split("\n").slice(-30).join("\n");
  log.scrollTop = log.scrollHeight;
}

function stageText(stage) {
  return ({
    download: "下载 Python 运行环境",
    extract: "解压",
    patch: "配置",
    "get-pip": "安装 pip",
    deps: "安装运行依赖",
  })[stage] || stage;
}

async function refreshStatus() {
  let st;
  try { st = await window.kevrai.bootstrapStatus(); }
  catch (e) { $("#bs-status").innerHTML = `<span class="err">状态检测失败：${e.message}</span>`; return; }

  const lines = [];
  if (st.python) {
    lines.push(`Python 解释器：<b class="ok">已找到</b>（${st.python}）`);
  } else {
    lines.push(`Python 解释器：<b class="err">未找到</b>`);
  }
  if (st.deps_missing) {
    lines.push(`运行依赖：<b class="warn">缺失</b>（后端启动时报 ModuleNotFoundError）`);
  } else if (st.python) {
    lines.push(`运行依赖：<b>待验证</b>`);
  }
  $("#bs-status").innerHTML = lines.join("<br/>");

  const isWin = st.platform === "win32";
  $("#btn-install").hidden = !!st.python || !isWin;
  $("#btn-deps").hidden = !st.deps_missing;

  if (!st.python && !isWin) {
    $("#bs-tip").innerHTML =
      "当前系统（Linux/macOS）暂不支持软件内自动安装 Python，请手动执行：<br/>" +
      "<code>python3 -m pip install -r python/requirements.txt</code>（安装目录见软件文档），完成后点「重试启动」。";
  }
  if (st.stderr_tail && st.stderr_tail.length) {
    appendLog(st.stderr_tail.join("\n"));
  }
}

function wire() {
  window.kevrai.onBootstrapProgress((p) => {
    if (!p || typeof p !== "object") return;
    const bar = $("#bs-bar");
    bar.hidden = false;
    $("#bs-bar-fill").style.width = `${Math.max(0, Math.min(100, p.pct || 0))}%`;
    $("#bs-stage").textContent = `${stageText(p.stage)}… ${p.text || ""}`;
    appendLog(p.text);
  });

  $("#btn-install").addEventListener("click", async () => {
    setBusy(true);
    $("#bs-stage").textContent = "开始安装…";
    try {
      await window.kevrai.installPythonRuntime();
      $("#bs-stage").textContent = "安装完成，正在进入软件…";
    } catch (e) {
      appendLog("安装失败：" + e.message);
      $("#bs-stage").textContent = "安装失败，可查看下方日志后重试";
      setBusy(false);
    }
  });

  $("#btn-deps").addEventListener("click", async () => {
    setBusy(true);
    $("#bs-stage").textContent = "正在安装运行依赖…";
    try {
      await window.kevrai.installPythonDeps();
      $("#bs-stage").textContent = "安装完成，正在进入软件…";
    } catch (e) {
      appendLog("安装失败：" + e.message);
      $("#bs-stage").textContent = "安装失败，可查看下方日志后重试";
      setBusy(false);
    }
  });

  $("#btn-retry").addEventListener("click", async () => {
    setBusy(true);
    $("#bs-stage").textContent = "正在重新启动后端…";
    try {
      await window.kevrai.bootstrapRetry();
      $("#bs-stage").textContent = "启动成功，正在进入软件…";
    } catch (e) {
      appendLog("启动失败：" + e.message);
      $("#bs-stage").textContent = "启动仍失败：请先完成环境安装";
      setBusy(false);
      refreshStatus().catch(() => {});
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  refreshStatus().catch(() => {});
});
