# 打包指南（Windows 11 安装包）

> 本开发环境为 Linux，无法直接产出 Windows .exe（PyInstaller 不支持跨平台交叉编译）。
> 因此提供**一键构建脚本**：在任意一台 Windows 11 机器上双击运行即可产出安装包。

## 一、准备工作（一次性）

| 工具 | 说明 |
|---|---|
| Python 3.10 ~ 3.12 | 安装时勾选 Add to PATH |
| Git | 默认安装即可 |
| Inno Setup 6 | https://jrsoftware.org/isdl.php （默认路径安装，脚本自动查找） |
| NVIDIA 驱动 | 构建机不强制需要显卡，运行时机器需要 |

## 二、一键构建

```bat
cd H3Studio
packaging\build_windows.bat
```

脚本做的事：
1. 创建 `.venv` 虚拟环境
2. 安装 PyTorch CUDA 12.4 版（约 2.5GB）
3. 安装 `requirements.txt`（清华镜像加速）+ PyInstaller
4. PyInstaller 打包为绿色版 `dist\H3Studio\`
5. Inno Setup 编译安装包 `packaging\Output\H3Studio-Setup.exe`

产物：
- **安装包**：`packaging\Output\H3Studio-Setup.exe`（含全部运行库，解压即用，约 1.5~2GB）
- **绿色版**：`dist\H3Studio\H3Studio.exe`

## 三、为什么安装包这么大？

PyTorch CUDA 运行库本身约 2.5GB，这是所有本地 AI 推理软件的共同现状（ComfyUI 整合包同理）。
模型权重**不**打进安装包（35~144GB 不等），由用户在软件内按需下载。

## 四、常见问题

- **pip 装 torch 慢/失败**：脚本已指定 PyTorch 官方索引；如仍慢可手动先 `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- **PyInstaller 报 hidden import**：spec 里已列 diffsynth/modelscope/bitsandbytes 等；若 diffsynth 升级新增依赖，把模块名加进 `hiddenimports`
- **Inno Setup 找不到中文语言文件**：删除 setup.iss 中 chinesesimplified 行即可（会用英文）
- **杀毒误报**：PyInstaller 产物偶发误报，属已知现象，可加数字签名解决

## 五、用户侧安装要求

- Windows 10/11 x64
- 加速硬件：NVIDIA 显卡（CUDA，默认路线）/ AMD 显卡（ROCm）/ 华为昇腾（torch-npu），推荐 8GB 显存以上 + 最新驱动
- 模型目录所在磁盘剩余 ≥100GB（建议 NVMe SSD）
- 内存 ≥16GB（建议 32GB）
