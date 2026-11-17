# Kevrai Omni v4.18.1

> 本版本由 GitHub Actions 原生构建并发布 Linux AppImage/deb、Windows NSIS 安装器 + 便携 zip、macOS dmg（x64 + arm64）。安装包内置冻结 sidecar 与 pip-runner，首次启动不需要单独安装 Python。
>
> 4.18.1 是对 4.18.0 的全面质量审计版本：静态检查、全量测试、实机安装运行、逐页面截屏检查、接口实测与目录链接存活审计之后，修复了以下真实问题。

## 修复的真实问题

- **测试污染真实用户数据（隐患）**：`test_fuzz.py` 等约 20 个测试文件自建 `TestClient` 时未隔离数据目录，对 `/api/settings` 的模糊测试写入会泄漏进真实的 `~/.local/share/KevraiOmni`——开发者本地跑完测试后，正在使用的应用会被写入 `model_dir="C:\Windows"`、`download_dir="abc"` 之类的夹具值。conftest 新增会话级数据目录沙箱（XDG/APPDATA 指向临时目录），既堵住泄漏，又不改变既有的逐测试隔离语义。
- **mypy 类型错误**：`app/hub/registry.py` 中 `warnings` 变量在同一作用域被重复注解（no-redef），合并为单一注解。
- **Linux 桌面窗口关联失效**：未设置 `desktopName`，桌面环境无法把运行中的窗口关联到 `.desktop` 条目（任务栏图标/窗口分组失效）。package.json 补 `desktopName`，electron-builder 启用 `syncDesktopName`。
- **卡片描述行距过紧**：实机截屏像素分析显示两行文字墨迹间仅 1px 间隙，视觉拥挤。行高 1.55 → 1.7，并补写标准 `line-clamp` 属性（新 Chromium 逐步以标准属性取代 `-webkit-` 前缀）。

## UI 精修（新增 ui-polish.css 呈现层，不改行为）

沿用 Cherry Studio 设计四原则（中性优先 / 语义色 / 内容先于装饰 / 表面色阶承担层级），针对实机截屏评审的弱点逐项精修：

- 卡片元数据标签：10.5px 透明底 → 11px + 薄表面 + 边框，VRAM/许可证标签带语义色描边，对比度与可读性提升。
- 首次启动引导弹窗：加宽至 600px，三个步骤改为独立卡片、间距舒展，操作区以分隔线独立成区。
- 侧边栏：分组标题 10px/半透明 → 11px/全不透明，分组间距加大，菜单项 36px → 38px，选中态加字重。
- 浅色主题：浮层/卡片补投影层次（全白表面下唯一可靠的层级信号），卡片边框略加深。
- 卡片网格间距 14px → 16px。

## 开发脚本清理

- 删除死代码：`generate_sbom.py` 未使用的 `urllib.request`/`npm_audit` 导入与 `now` 变量；`verify_catalog_urls.py` 构造后未使用的 `req`。
- 5 处 `subprocess.run` 补显式 `check=False`（这些调用本就手动检查 returncode，显式化消除歧义）。
- 2 处 `json.load(open(...))` 改为 with 上下文管理，避免文件句柄泄漏。
- `startswith` 三次调用合并为元组单次调用；带 shebang 的脚本补可执行位；ruff 自动修复 17 处（导入排序、datetime.UTC 别名、多余 noqa 等）。

## 验证（全部在本机实际执行）

- pytest 全量：**2747 passed**（含新增隔离修复后的复跑）。
- 前端：`node --check` 72 个 JS 文件通过；renderer 测试 **450/450**；CSS 主题测试 21/21。
- 静态检查：ruff（CI 口径）全过；mypy 85 个源文件 0 错误。
- 冒烟测试 `scripts/smoke.sh`：9/9 步通过。
- 冻结 sidecar（PyInstaller onedir）+ pip-runner 实际构建并启动：`/api/health` 200，pip-runner  dry-run 安装回归通过。
- Electron 实机运行（xvfb）：21 个页面逐一点击遍历，控制台 0 报错；核心 API 实测（gpu/recommend/availability/params.resolve/agent.sessions/knowledge/engines/settings/hub.sources 等）全部正常。
- 目录链接存活审计抽样 60 个 URL：OK=37、REDIRECT_OK=23，无死链。
- npm 依赖安全审计：无已知漏洞。
- Linux AppImage + deb 实际构建、安装并运行验证。
