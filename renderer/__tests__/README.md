# Renderer 单元测试（前端 QA）

为渲染层的**纯逻辑**模块提供可重复的单元测试，使用 Node 内置 test runner 与 jsdom。

## 运行

```bash
# 从仓库根目录
npm run test:renderer
# 或直接
node --test renderer/__tests__/*.test.js
```

首次运行需已执行 `npm install`（jsdom 在 devDependencies 中）。

## 覆盖范围

| 测试文件 | 被测模块 | 覆盖点 |
|---|---|---|
| `virtual-grid.test.js` | `modules/virtual-grid.js` | 窗口化挂载、滚动/缩放的 rAF 帧节流、几何宽度来源（viewport 内宽，修复右列被滚动条裁切）、键盘 Enter/Space 激活、near-end 增量加载与 loading 门控、appendItems 保位、destroy 清理 |
| `net.test.js` | `modules/net.js` | IPC 响应 `unwrap` 三种形态与兜底、`escapeHtml`、`escapeAttr` |
| `state.test.js` | `modules/state.js` | 状态合并、订阅通知、退订、订阅者抛错隔离 |
| `debounce.test.js` | `modules/debounce.js` | trailing 触发与最新参数、等待期不触发、cancel |

## 约定

- jsdom 不做布局，`clientWidth/clientHeight` 通过 `helpers/dom.js` 的 `stubSize` 固定；`requestAnimationFrame` 为可手动 `flushFrames()` 的确定性实现，避免真实定时器带来的抖动。
- 这里只测**行为与逻辑**，不测视觉样式（视觉归 `styles.css`，由对应认领方负责）。
- 建议在 CI 中加入 `npm run test:renderer`（CI 工作流由整合方维护，此处不擅自改动）。
