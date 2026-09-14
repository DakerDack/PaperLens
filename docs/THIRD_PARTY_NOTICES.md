# 第三方归属、数据许可与 AI 使用说明

核验日期：2026-09-10。项目权利人本次明确选择 MIT；根目录 LICENSE 仅覆盖项目自有代码和文档，不替代第三方组件、论文和模型条款。PaperLens 为个人项目，不是腾讯官方产品，不表示任何第三方为本项目背书。

## 核心组件

前端运行时包的完整许可文本保存在 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。若单独交付 `frontend/dist`，必须同时附带该文件、本说明及根目录 LICENSE；仅有项目 MIT 不能替代组件许可证。历史浏览器阶段交付为源码和审计材料；Windows 桌面候选包的额外运行时与许可范围见下方“Windows 桌面打包”。候选包尚未最终放行。

| 组件 / 实测版本 | 归属和许可 | 使用边界 |
| --- | --- | --- |
| MinerU 3.4.5 | MinerU Team / OpenDataLab；[官方许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md) | Apache 2.0 加商业规模及在线服务归属附加条款；本机 wheel 内 LICENSE.md 与此一致，不称为纯 Apache 2.0。独立 CLI 安装，不分发模型权重；模型及其依赖另守各自条款。 |
| PDF.js / pdfjs-dist 5.5.207 | Mozilla 与贡献者；[Apache 2.0](https://github.com/mozilla/pdf.js/blob/master/LICENSE) | 浏览器渲染 PDF；分发构建包时保留所含许可证和归属。 |
| Hy3 | Tencent Hunyuan；[官方项目及许可证入口](https://github.com/Tencent-Hunyuan/Hy3) | 本项目通过 TokenHub 调用，不分发或本地部署权重。API 使用按腾讯云服务条款和账户授权执行，项目 MIT 不授予模型/API 权利。 |
| OpenAI Python SDK 2.54.0 | OpenAI；Apache 2.0 | 仅作为 OpenAI 兼容协议客户端连接 TokenHub，不表示调用 OpenAI 模型。 |
| FastAPI 0.141.1、Pydantic 2.13.4、pydantic-settings 2.15.0 | 各项目贡献者；MIT | 后端 API 与数据验证。 |
| Uvicorn 0.52.4、Starlette 1.6.0 | 各项目贡献者；BSD-3-Clause | 本地 HTTP 服务。 |
| pdfplumber 0.11.10、pdfminer.six 20260107 | 各项目贡献者；MIT | 普通文本 PDF 降级解析。 |
| rank-bm25 0.2.2、python-multipart 0.0.32 | 各项目贡献者；Apache 2.0 | 检索与上传。 |
| React / React DOM 19.2.0 | Meta 与贡献者；MIT | 前端组件。 |
| lucide-react 1.34.0 | Lucide / Feather 贡献者；ISC / MIT，见包内 LICENSE | 图标。 |
| Vite 7.3.6、Vitest 4.1.11、Testing Library | 各项目贡献者；MIT | 开发、构建和测试。 |
| TypeScript 5.9.2、Playwright 1.62.1 | Microsoft 与贡献者；Apache 2.0 | 类型检查和浏览器测试/演示。 |

完整传递依赖以 `requirements.lock`、`frontend/package-lock.json` 和安装包中的 LICENSE/NOTICE 为准。尤其 certifi 为 MPL-2.0，tqdm 为 MPL-2.0 AND MIT，pypdfium2/PDFium 含多项依赖许可，NumPy 和 Pillow 也包含附属组件声明。不要把这些文件替换成项目 MIT。仓库不打包 Python 环境、node_modules、MinerU 权重或 Chromium；若另行分发运行时/二进制，必须随包带上全部对应许可证和 NOTICE。

安装方法核对：[Python venv](https://docs.python.org/3/library/venv.html)、[Node 官方下载](https://nodejs.org/en/download)、[MinerU 官方 quick start](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/index.md)。MinerU 的 Windows Python 与硬件限制需按官方资料及锁定版本核对；本轮未重建/运行真实 MinerU。

TokenHub 的 API 概览首次访问失败后，已通过官方协议入口核验 [TokenHub 服务条款](https://cloud.tencent.com/document/product/301/129852)（页面更新 2026-08-21，2026-08-25 生效）。Hy3 列于腾讯云自有模型；用户需保护凭证、拥有输入处理权限并自行审核输出及使用行为。该核对不等于替用户同意条款或证明本项目已满足公开运营要求。

## 论文与评测材料

`eval/live_cases.json` 保存来源 URL、DOI、CC BY 标记、历史核验日期和材料 SHA256。只含人工整理的短摘要证据、改写输出与错误注入，不存论文全文；页码为评测构造，不等同真实论文 PDF 页码。以下署名与正式标题按出版方页面核对，所有作者名单及具体适用 CC BY 版本以链接页面为准。归属不表示原作者认可这些改写或错误注入。

| ID | 原作者 / 正式标题与来源 |
| --- | --- |
| dev-01 | Dzogang, Lightman, Cristianini (2018). [Diurnal variations of psychometric indicators in Twitter content](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0197002) |
| dev-02 | Kross et al. (2013). [Facebook Use Predicts Declines in Subjective Well-Being in Young Adults](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0069841) |
| dev-03 | West et al. (2017). [Playing Super Mario 64 increases hippocampal grey matter in older adults](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0187779) |
| dev-04 | Attwood et al. (2012). [Glass Shape Influences Consumption Rate for Alcoholic Beverages](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0043007) |
| dev-05 | Holt-Lunstad, Smith, Layton (2010). [Social Relationships and Mortality Risk: A Meta-analytic Review](https://journals.plos.org/plosmedicine/article?id=10.1371/journal.pmed.1000316) |
| holdout-01 | Kim et al. (2018). [Lack of sleep is associated with internet use for leisure](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0191713) |
| holdout-02 | Chang, Kajackaite (2019). [Battle for the thermostat: Gender and the effect of temperature on cognitive performance](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0216362) |
| holdout-03 | Dror et al. (2022). [Pre-infection 25-hydroxyvitamin D3 levels and association with severity of COVID-19 illness](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0263069) |
| holdout-04 | Bhusal et al. (2021). [Health literacy and associated factors among undergraduates: A university-based cross-sectional study in Nepal](https://journals.plos.org/globalpublichealth/article?id=10.1371/journal.pgph.0000016) |
| holdout-05 | Mora et al. (2011). [How Many Species Are There on Earth and in the Ocean?](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.1001127) |

上述文章页面均给出 Creative Commons Attribution 授权，见各页面 Copyright 段及 [PLOS 许可政策](https://plos.org/open-science-policies/)。文章许可不自动覆盖其受限底层数据，也不等于学术结论有效性审核。本次没有下载或分发原始科研数据。manifest 中“untouched holdout”是构造时历史描述；现状为已查看固定样本，不能再称盲测。

`backend/tests/fixtures/simple_2page.pdf` 为项目合成的两页短文本；`scanned_1page.pdf` 为无文本测试页；`corrupt.pdf` 为故意损坏的负例。它们不是私密论文，按项目 MIT 提供。其内容与哈希在阶段 8 扫描记录核对。生产上传仅接受用户有权处理的材料，不得上传未公开稿件、审稿材料、秘密或许可不明全文。

## AI 使用说明

项目开发、文档和测试使用 Codex 等 AI 编程辅助；人类项目维护者负责需求、权限、审查及最终交付，AI 不列为学术作者。运行时 Live 使用 Hy3 生成和语义判断；引用、页码、分数和门槛由确定性代码核验/计算。模型可能遗漏风险、误判或生成无依据内容，不能代替专家和论文原文。

Mock 和演示预置结果只用于交互与错误路径验证；视频和画面必须持续标识。不得以预置结果证明模型效果。导出保留来源、AI 辅助及模型模式说明；用户对后续引用、许可、公开披露和学术诚信负责。应用为本机单用户原型，不声称满足公开在线运营的全部义务。


## Windows 桌面打包（D4a 候选包）

候选版 0.1.0 随包保留项目 `LICENSE`、本说明、`THIRD_PARTY_LICENSES.md` 和 `licenses` 目录中的组件原始声明，包括 Python、proxy_tools、Inno Setup 及 Python/原生传递依赖。WebView2 SDK 的 LICENSE/NOTICE 正文保留在本文件下方。复制或分发应用目录时须一并保留这些文件，不能只取 EXE。此处记录已有归属与文本，不替用户授予第三方模型、服务或论文的使用权。

D5 材料准备仅澄清交付范围，不改下方原始许可证正文。D4a 冻结包仍含 D4a 时点的本文件；最终交付须重建或重新整理对应发行材料并核对新的摘要，不能声称当前包已包含本次文档更新。安装与干净环境验证仍为 PENDING，见 [验收记录](WINDOWS_DESKTOP_ACCEPTANCE.md)。

桌面发行含 Python 3.13、pywebview 6.2.1（BSD-3-Clause）、pythonnet 3.1.0、clr_loader 0.3.1 和其原生桥。构建通过 PyInstaller 6.22.2（GPL 分发例外），安装器通过 Inno Setup 6.7.3；完整发行许可文件随包保存在 licenses，前端文本保留 THIRD_PARTY_LICENSES.md。原有“不分发 Python 环境”说明仅适用于先前源码交付，桌面包包含解释器。

WebView2 Evergreen x64 离线安装器来自微软，构建前核验微软 Authenticode 签名。已有运行时复用，缺失时安装；其更新由微软运行时管理，不是 PaperLens 自动更新功能。运行时再分发遵守微软随包许可；共享运行时卸载时保留。

Inno Setup Copyright (C) 1997-2026 Jordan Russell; Portions Copyright (C) 2000-2026 Martijn Laan. https://jrsoftware.org/ 。官方许可：https://jrsoftware.org/files/is/license.txt 。安装包未经 PaperLens 发布者代码签名，不宣称受信任发布者。

proxy_tools 0.1.0 的发行元数据标 MIT，但上游固定提交 db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6 的 LICENSE.txt 为 BSD；应随包保留该原文（含原有重复句），SHA256 a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4，不能用元数据替代。尚未收集该正文或任一必要原生附属许可时不得交付。


### WebView2 SDK 1.0.3856.49 原始许可及声明

来源：https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49 。Core/WinForms DLL 已与官方归档逐字节核对。以下为该版本 LICENSE.txt 和 NOTICE.txt 正文（仅去除行尾空白以符合仓库差异检查）。

```text
Copyright (C) Microsoft Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * The name of Microsoft Corporation, or the names of its contributors
may not be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

```text
NOTICES AND INFORMATION
Do Not Translate or Localize

This software incorporates material from third parties. Microsoft makes certain
open source code available at https://3rdpartysource.microsoft.com, or you may
send a check or money order for US $5.00, including the product name, the open
source component name, and version number, to:

Source Code Compliance Team
Microsoft Corporation
One Microsoft Way
Redmond, WA 98052
USA

Notwithstanding any other terms, you may reverse engineer this software to the
extent required to debug changes to any libraries licensed under the GNU Lesser
General Public License.

----------------------------------------------------------------

Antlr3.Runtime 3.5.2-rc1 - BSD 3-Clause

[The "BSD license"]
Copyright (c) 2011 The ANTLR Project
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 3. Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---------------------------------------------------------

---------------------------------------------------------

StringTemplate4 4.0.9-rc1 - BSD 3-Clause

[The "BSD license"]
Copyright (c) 2011 The ANTLR Project
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 3. Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---------------------------------------------------------

```
