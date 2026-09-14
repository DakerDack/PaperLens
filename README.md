<div align="center">

# PaperLens

### 读懂论文，让每一句解读都有据可循。

基于腾讯混元 Hy3 的学术阅读与证据审计工作台<br>
从 PDF 到结构化解读，从原文核验到对话式修订，在一个界面完成。

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![React](https://img.shields.io/badge/React-19-149ECA?style=flat-square&logo=react&logoColor=white)](frontend/package.json)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=flat-square&logo=typescript&logoColor=white)](frontend/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](backend/app/main.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-8B5CF6?style=flat-square)](LICENSE)

[Windows 桌面版](#windows-桌面版候选-010) · [源码快速开始](#快速开始) · [功能亮点](#功能亮点) · [项目资料](#项目资料)

</div>

---

## 为深入阅读而设计

论文阅读不仅是提取摘要，还包括理解研究方法、核对关键结论，以及整理自己的表达。**PaperLens 将阅读、证据和修订放在同一个工作台**：一边查看 PDF 原文，一边阅读结构化解读，随时追溯句子的来源，再通过自然语言完成修改。

适合希望快速建立论文全貌的学生、需要核对论据的研究者，以及正在整理文献笔记的知识工作者。

## 功能亮点

| | 功能 | 你可以做什么 |
| :---: | --- | --- |
| 📄 | **五区结构化解读** | 按研究问题、研究方法、主要结果、研究局限与通俗解释组织论文内容 |
| 🔎 | **主张级证据追溯** | 从解读句子定位引用摘录与 PDF 页码，在阅读中核对依据 |
| 🧭 | **快速检查与完整审计** | 先查看证据检查，再进行八维完整审计与评分 |
| 💬 | **对话式修订** | 用自然语言提出句子或全文修改意图，先预览补丁，再确认应用 |
| 🕘 | **版本记录与回退** | 保留修改历史，比较阅读与修订过程中的不同版本 |
| 📤 | **Markdown 导出** | 将当前解读导出为便于整理、分享和继续编辑的文档 |

### 一条连贯的阅读流程

```mermaid
flowchart LR
    A[上传 PDF] --> B[生成五区解读]
    B --> C[追溯原文证据]
    C --> D[检查与审计]
    D --> E[对话式修订]
    E --> F[预览并确认]
    F --> D
    F --> G[导出 Markdown]
```

**原文、解读、操作并排呈现。** 在三栏工作台中，阅读与核验无需反复切换页面；修订由你确认，历史版本随时可回看。

> 🎬 [观看约 50 秒的工作台演示](reports/stage8_demo.webm) · 演示采用本地 API 与 Mock 预置示例。

## Windows 桌面版（候选 0.1.0）

当前为 **待完成安装验收的候选包，尚未最终放行或公开发布**。安装器生成、资源完整性和验收脚本审查已通过；实际安装、升级、卸载及没有 Python/Node 的 Windows 环境验证均为 **PENDING**。最新状态、产物摘要及验证边界见 [桌面验收记录](docs/WINDOWS_DESKTOP_ACCEPTANCE.md)。历史浏览器演示与 Mock 结果不能替代这些验证。

候选包面向 Windows 10 22H2 / Windows 11 x64，包含 Python 解释器和已构建前端，设计上无需用户安装 Python、Node 或打开终端。需要 .NET Framework 4.8 及 WebView2；安装器会检测前者，复用已有 WebView2 或尝试安装随包运行时。上述安装分支仍待目标机器实测。首版采用文本 PDF 解析，不含 OCR/MinerU 模型，扫描 PDF 可能无法提取文本。

### 安装与首次使用

以下是候选版操作说明，当前只供指定测试环境验证：

1. 从交付者取得 `PaperLens-0.1.0-windows-x64-setup.exe`，核对交付记录中的 SHA256。当前没有公开 Release 下载入口；候选安装包未做 PaperLens 发布者代码签名，不能宣称受信任发布者。出现安全提示时先核实来源，不关闭系统防护。
2. 关闭已运行的 PaperLens，双击安装器，选择安装位置及是否创建桌面快捷方式。默认安装到当前用户的 `%LOCALAPPDATA%\Programs\PaperLens`。安装失败时保留完整错误，不能以出现目录或图标判断成功。
3. 安装完成后双击桌面或开始菜单图标，进入独立窗口。
4. 离线体验时打开“模型设置”，选择“Mock · 离线演示数据”，保存并关闭、重新打开应用。普通入口默认 Live；未配置 Key 时真实模型请求会失败，不会自动退回 Mock。
5. 导入有权处理的文本 PDF，生成解读、定位证据、检查与审计、预览并应用修订、回退和导出 Markdown。Mock 是预置结果，用来检查流程，不代表对任意论文的真实理解或模型质量。

### 配置自己的 Hy3 Key

桌面版在“模型设置”中选择 Live 并输入自己的 Key；凭据保存在当前 Windows 用户的系统凭据存储中，**不需要创建或修改 `.env`**。保存成功不代表 Key 已通过供应商验证；当前桌面候选包尚未验证真实模型调用。使用 Live 会把相关论文内容及上下文发送给服务商，并可能产生费用，当前离线验收不执行此操作。

Key 输入框留空表示保留已有 Key；删除须点击“清除 Key”。保存、替换和清除均在重启后生效，当前实例继续使用已加载的配置，因此清除后应关闭整个窗口再重新打开。界面只显示是否配置，不回显已保存 Key；读取失败显示“状态未知”，应重新读取，不要当成已经清除。清除 Key 不删除论文或项目。

### 数据、升级与卸载

正常桌面入口的数据根为当前用户 `%LOCALAPPDATA%\PaperLens`：`data` 保存项目文件和数据库，`desktop-state.json` 保存最近项目及运行模式，`webview` 保存 WebView 缓存。路径由 Windows Known Folder 定位，与安装目录分离。迁移或备份应先正常关闭应用，再备份数据根；Key 不在此目录，换 Windows 账户或电脑须重新配置。

升级前关闭应用；卸载使用 Windows“已安装的应用”中的 PaperLens。安装器按保留数据及系统凭据设计，卸载不会替用户清除 Key 或移除共享 WebView2。**升级、卸载后重装及数据保留尚未实测，暂不能作为已验证保证**。首版不提供自动更新，最终交付前必须补齐这些检查。

遇到重复启动提示时切换到已有窗口；启动或退出失败时保存错误码和截图，不删除数据目录排障。`--offline-test` 仅供开发验收，每次使用新临时数据和合成凭据，不能用它验证项目跨启动保留。桌面验证步骤见 [验收记录](docs/WINDOWS_DESKTOP_ACCEPTANCE.md)，第三方声明与随包许可证说明见 [许可说明](docs/THIRD_PARTY_NOTICES.md)。

## 快速开始

以下是**开发者源码／浏览器模式**，与上方桌面安装方式分开。命令适用于 Windows PowerShell；准备 Python 3.13、Node.js 24 和 Git，在本地启动工作台。

### 1. 获取项目

```powershell
git clone https://github.com/DakerDack/PaperLens.git
Set-Location PaperLens
```

### 2. 安装依赖

```powershell
py -3.13 -m venv .venv313
& '.\.venv313\Scripts\python.exe' -m pip install -r requirements.lock
& '.\.venv313\Scripts\python.exe' -m pip check

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}

Push-Location frontend
npm.cmd ci
Pop-Location
```

### 3. 启动示例工作台

在仓库根目录打开第一个终端，启动后端。此配置使用 **Mock 预置结果**与 pdfplumber 文本解析，适合体验仓库自带示例。

```powershell
$env:PAPERLENS_MODEL_MODE = 'mock'
$env:PAPERLENS_DATA_DIR = './data/demo'
$env:MINERU_COMMAND = './.venv-mineru-disabled/Scripts/mineru.exe'

& '.\.venv313\Scripts\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

在仓库根目录打开第二个终端，启动前端：

```powershell
Set-Location frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

| 入口 | 地址 |
| --- | --- |
| 阅读工作台 | http://127.0.0.1:5173 |
| 交互式 API 文档 | http://127.0.0.1:8000/api/docs |

### 4. 体验完整流程

1. 上传 [`simple_2page.pdf`](backend/tests/fixtures/simple_2page.pdf)，确认处理权限。
2. 生成五区解读，查看快速检查与完整审计。
3. 点击解读句子，查看对应页码和引用摘录。
4. 输入示例修改意图 **“只追加安全标点”**，预览并接受修订。
5. 重新审计、查看历史版本，导出 Markdown。

两个终端分别按 `Ctrl+C` 即可停止服务；项目数据保存在 `PAPERLENS_DATA_DIR` 指定的本地目录。

## 源码／浏览器模式连接 Hy3

**Live 模式**通过腾讯云 TokenHub 调用 Hy3，用于真实模型生成、审计与修订。在本机 `.env` 中配置：

```dotenv
PAPERLENS_MODEL_MODE=live
HY3_API_KEY=your_tokenhub_api_key
```

在新的后端终端中，从仓库根目录启动：

```powershell
& '.\.venv313\Scripts\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

若沿用示例终端，先执行 `Remove-Item Env:PAPERLENS_MODEL_MODE -ErrorAction SilentlyContinue`，让模式配置从 `.env` 加载。页面中的模式标识可用于确认当前运行方式。

Live 请求会将相关来源内容、解读和修订上下文发送至模型服务。请使用有权处理的材料，并将 API Key 保留在本机 `.env` 中。模型调用费用按服务商计费规则结算。

## 设计与技术

PaperLens 将语言理解与可核验的工程环节分开组织：**Hy3 负责生成与语义分析，代码负责引文核验、页码关联及评分计算**。

| 层次 | 技术与职责 |
| --- | --- |
| 交互界面 | React 19、TypeScript、Vite，组织阅读、审计与修订流程 |
| PDF 阅读 | PDF.js，呈现原文、页码跳转与文本查找 |
| 服务端 | FastAPI、Pydantic，提供接口与结构化数据契约 |
| 文档解析 | pdfplumber 文本解析；可通过独立 CLI 接入 MinerU |
| 模型服务 | Hy3，经 TokenHub 完成生成、语义审计与修订 |
| 证据处理 | 引文匹配、BM25 检索及确定性核验 |
| 项目存储 | 本地文件与 SQLite，记录项目内容及版本历史 |
| 工程验证 | pytest、Vitest、Playwright |

<details>
<summary><strong>开发者：运行检查</strong></summary>

在仓库根目录运行后端与评测测试：

```powershell
& '.\.venv313\Scripts\python.exe' -m pytest backend/tests eval/test_eval.py -q -p no:cacheprovider
```

前端检查与构建：

```powershell
Set-Location frontend
npx.cmd --no-install playwright install chromium
npm.cmd run test -- --run
npm.cmd run typecheck
npm.cmd run build
npx.cmd --no-install playwright test
```

应用 Python 依赖由 `requirements.lock` 固定，前端依赖通过 `package-lock.json` 与 `npm ci` 安装。MinerU 使用独立工具环境，通过 `MINERU_COMMAND` 指定 CLI 路径。

</details>

## 项目资料

本项目参与“开放式场景：AI 应用与评判标准设计”，配套提供场景分析、评测材料和可追溯的实验记录。

| 资料 | 内容 |
| --- | --- |
| [任务一分析报告](docs/task1_analysis.md) | 应用场景、八维评价标准、实验设计与分析 |
| [项目设计](docs/paperlens_project_proposal.md) | 产品目标与工作流设计 |
| [开发计划](docs/DEV_PLAN.md) | 实现任务、版本演进与验证记录 |
| [评测样本](eval/live_cases.json) | 公开材料与样本清单 |
| [评测结果](reports/stage7_full_results_auditv8_scope_r1.md) | 逐项结果与记录索引 |
| [评测工具](eval/run_eval.py) · [报告工具](eval/build_report.py) | 评测执行与报告重建 |

## 开源与致谢

PaperLens 的自有代码采用 **[MIT License](LICENSE)**，欢迎学习、使用和参与改进。

感谢腾讯混元 Hy3 及开源社区提供的模型与工具支持。第三方组件和材料归属见 [第三方说明](docs/THIRD_PARTY_NOTICES.md)。本项目为个人开源项目。

---

<div align="center">

**PaperLens · 从阅读到理解，从结论到证据。**

如果这个项目对你有帮助，欢迎点亮一颗 ⭐

</div>
