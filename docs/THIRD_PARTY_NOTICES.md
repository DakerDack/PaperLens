# 第三方归属、数据许可与 AI 使用说明

核验日期：2026-09-10。项目权利人本次明确选择 MIT；根目录 LICENSE 仅覆盖项目自有代码和文档，不替代第三方组件、论文和模型条款。PaperLens 为个人项目，不是腾讯官方产品，不表示任何第三方为本项目背书。

## 核心组件

前端运行时包的完整许可文本保存在 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。若单独交付 `frontend/dist`，必须同时附带该文件、本说明及根目录 LICENSE；仅有项目 MIT 不能替代组件许可证。本次交付是源码和审计材料，不是独立二进制安装包。

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
