# Kevrai Omni v4.17.7 发布说明

本版是一次**安全性/健壮性强化**版本，修复 v4.17.6 深度审查中确认的 17 个问题，
并引入 35 项回归测试锁定这些修复。

## 高危（P0）

### 1. 下载器默认改为 default-deny
`app/downloader.py` 的 `_enforce_allowlist = bool(allowed_hosts)` 使“强制白名单”
成为**可选**行为——默认放行任意 http(s) 主机，而 `SECURITY.md` 一直宣称
“default-deny at every download entry point”。代码与文档矛盾，构成 SSRF/任意
外联面。

v4.17.7 起默认强制白名单；需要旧行为必须显式传 `enforce_allowlist=False`，
且**携带 Authorization 凭据的请求永远强制白名单**（防止 Bearer token 外泄）。
回环地址（127.0.0.0/8、::1、localhost）豁免白名单但仍不允许私网/云元数据地址。

### 2. 发布标签内自带失败测试
`tests/test_v405_asr_runtime.py::test_bad_repo` 在 v4.17.6 上失败
（`1 failed, 2629 passed`）。v4.17.6 为兼容 faster-whisper 尺寸别名把校验放宽为
“非空”，但测试未同步。

v4.17.7 改为**严格校验**：接受官方尺寸别名（`tiny`…`large-v3-turbo`）或严格的
`owner/name` 仓库 id，拒绝 `../../etc`、`https://host/x`、`a/b/c` 等。既让测试
恢复通过，也堵住了模型参数被拼进本地路径的注入面。

## 中危（P1）

### 3. tar 解压回退分支无安全过滤器
`engines.py` 用 `filter="data"`，但 Python < 3.12 的 `except TypeError` 回退分支
调用**无过滤**的 `extractall` —— 符号链接成员可被创建并用于写出。改为自实现
`_safe_extract_tar`：只写普通文件/目录，跳过符号链接/硬链接/FIFO/设备，每个目标
路径写入前重新 resolve 校验不逃逸。

### 4. 重复定义的死代码
`main.py` 中 `brain = None` 后紧跟 `def brain(...)`，`brain_stream` 同理——
pyflakes 报 redefinition，外层赋值永不生效。改为单次绑定并显式标注。

### 5. escapeHtml 分散在 12 个模块
12 份各自实现，其中 4 份不转义单引号，1 份对 `null` 输出字面量 "null"。
统一收口到 `renderer/modules/net.js` 单一实现，其余模块改为 import/re-export。

### 6. docx XML 解析的不安全回退
`knowledge_base.py` 在 defusedxml 缺失时静默回退到 stdlib XML 解析，重新打开
实体膨胀/XXE 面。defusedxml 本就是 pinned 依赖，缺失时改为**明确报错**。

### 7. 死逻辑 `f"..." or "..."`
`engines.py` 中 `f"git clone failed: {msg}" or "git clone failed"` —— 非空 f-string
永远为真，回退分支永不生效，且 msg 为空时产生悬空的 "git clone failed: "。
改为条件构造。

## 低危（P2）

- 8 处未使用导入 / 导入顺序（ruff 73 → **0**）
- `DiffusionPipeline` 重复定义
- `try/except/pass` 静默吞异常 → 改为 `log.warning`（engines、plugins）
- `assert` 承载运行时不变式 → 改为显式 `bool()` / 显式检查
- subprocess 使用裸命令名 → 新增 `_tool_path()` 用 `shutil.which` 固定绝对路径
- 弱哈希（sha1 用作 id/缓存键）→ 显式 `usedforsecurity=False`，FIPS 模式可用
- WebDAV 配置明文密码 0644 → 强制 0600，并在 rename 后再次收紧
- `hf-cdn.sufy.com` 钓鱼镜像仍作为预设出现 → **从预设注册表移除**（仍保留在
  硬拦截列表，用户粘贴也会被拒）
- bandit 高危 2 → **0**

## 额外发现并修复（v4.17.6 审查未覆盖）

### 磁盘预检的固定 2GB 预留
`DEFAULT_MIN_FREE_BYTES = 2GiB` 被当作固定值使用。在总容量 4GB 的机器/容器上
意味着**任何下载都被拒绝**（包括 200MB 的小模型）。改为自适应：
`min(2GiB, max(256MiB, 总容量/50))`，且启动预检与流中校验使用同一个值。

## 测试

| 项目 | v4.17.6 | v4.17.7 |
|---|---|---|
| pytest | 2629 passed, **1 failed** | **2667 passed, 0 failed** |
| ruff | 72 warnings | **0** |
| bandit 高危 | 2 | **0** |
| smoke.sh | 通过 | 通过 |

新增 `python/tests/test_v4177_hardening.py`（35 项）覆盖上述每一项修复：
默认拒绝与显式放行、凭据强制白名单、回环豁免、磁盘预留自适应、ASR 参数校验
正反例、tar 符号链接/硬链接/穿越、WebDAV 0600、钓鱼镜像不在预设中。

## 安装包

- Linux：`Kevrai-Omni-4.17.7-x86_64.AppImage`、`Kevrai-Omni-4.17.7-amd64.deb`
- 每个安装包附带 `.asc` GPG 分离签名、`SHA256SUMS.txt`、公钥
  `kevrai-release-signing.asc`

## 已知限制

- 签名使用**仓库内新生成的自签名 GPG 密钥**，不是商业代码签名证书：
  Windows/macOS 的 Authenticode / notarization 无法在此环境完成。
- Windows（NSIS）与 macOS（DMG）安装包需要对应操作系统构建
  （PyInstaller 与 electron-builder 均非交叉编译），本环境仅有 Linux。
