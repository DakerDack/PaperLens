# PaperLens 调研来源台账

核验日期：2026-08-21  
用途：为方案文档、README、评测说明和后续实验提供可追溯来源。

## 使用规则

- A：任务书、政府、官方模型/API/协议等一手规范。
- B：同行评审论文、正式论文页面或论文作者公开材料。
- C：商业产品官方页面，只能证明其公开功能和价格，不能证明实际效果。
- D：GitHub README、项目博客或其他发现材料，适合技术调研，不作为用户需求统计。
- 任何会变化的价格、模型状态、功能和条款，在正式提交前重新核验。
- 研究样本量不是目标市场规模，产品公开用户数也不是活跃用户数。

## 1. 课题与 Hy3

| ID | 等级 | 来源 | 支持的事实 | 局限 |
|---|---|---|---|---|
| T01 | A | 用户提供的《犀牛鸟开源-实战任务-混元大语言模型项目》PDF | 任务 1 的真实应用、5+ 维度、难例/反例、判别力、一致性、完整结果和交付要求 | 本地附件，无公开 URL |
| H01 | A | [Tencent-Hunyuan/Hy3](https://github.com/Tencent-Hunyuan/Hy3) | 295B/21B MoE、256K、BF16、8 GPU 部署建议、reasoning 参数、Apache 2.0 | 仓库数据可能随版本变化；官方 benchmark 仍需独立复现 |
| H02 | A | [TokenHub 语言模型调用概览](https://cloud.tencent.com/document/product/1823/130079) | Hy3 支持 Chat/Responses/Anthropic，广州和新加坡 Base URL | 平台持续更新，必须查询 `/v1/models` |
| H03 | A | [TokenHub 模型列表](https://cloud.tencent.com/document/product/1823/130051) | `hy3`、256K、最大输入 192K、最大输出 128K、结构化输出、Function Calling、Cache | 能力标签不等于每个字段在所有场景都无缺陷 |
| H04 | A | [TokenHub 模型价格](https://cloud.tencent.com/document/product/1823/130055) | 2026-08-19 Hy3 输入 1 元/M、输出 4 元/M、缓存 0.25 元/M；搜索 7/12 元每千次 | 价格会变，账单以控制台为准 |
| H05 | A | [TokenHub 联网搜索](https://cloud.tencent.com/document/product/1823/132358) | Hy3 支持 Responses 和 Chat 搜索，返回结构化来源 | PaperLens benchmark 建议关闭搜索以保证可复现 |
| H06 | A | [TokenHub Responses API](https://cloud.tencent.com/document/product/1823/135873) | `hy3` 在 `json_schema`/`json_object` 下可输出纯 JSON | 仍需实测 schema 复杂度和重试率 |
| H07 | A | [TokenHub 服务条款](https://cloud.tencent.com/document/product/301/129852) | 页面于 2026-08-21 展示的新版本称 Hy3 为腾讯自有模型，并规定必要处理、地域、保留、不得用于自身模型训练、输入授权、AI 标识与责任边界 | 新版本写明 2026-08-25 生效，今天尚未生效；正式提交和真实调用前再核验届时版本 |
| H08 | A | [TokenHub 内容安全防护](https://cloud.tencent.com/document/product/1823/134506) | 可将输入输出送 WAF 做提示注入、违规和敏感数据检测 | 是否开启由服务配置决定；可能增加处理路径 |

## 2. 用户需求与教育研究

| ID | 等级 | 来源 | 样本/关键发现 | 对 PaperLens 的意义与局限 |
|---|---|---|---|---|
| U01 | B | [Higher education students' perceptions of ChatGPT](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0315011) | 23,218 名学生，109 个国家和地区；主要用途含头脑风暴、总结和查找论文；简化信息有价值，可靠性评价较弱 | 支持大需求方向；便利抽样、地区不均、早期印象，不能代表深大学生 |
| U02 | B | [Perceived Challenges in Primary Literature](https://pmc.ncbi.nlm.nih.gov/articles/PMC5132374/) | n=69；方法、数据、术语和结论均是难点；结论难点在教学后由 7% 到 30% | 支持方法-数据-结论链，不只做术语解释；生物学硕士小样本 |
| U03 | B | [Reading between the GenAI lines](https://open-publishing.org/publications/index.php/APUB/article/view/2719) | 20 名论文作者；AI 摘要看似准确但常空泛，遗漏语境、方法、结果、限制和文献连接 | 直接支持错误分类；质性、小样本、地区集中 |
| U04 | B | [Evidence verification and information sources](https://revistas.usp.br/ep/en/article/view/245301) | n=30，实验组 20、对照 10；证据特征识别改善，但来源质量和可验证性仍不清晰 | 支持可视化核验和 AI 素养；小型前实验，不能推断普遍因果效果 |
| U05 | B | [Biology student motivations and challenges](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0251275) | 焦点组 n=11；困难包括大图景、术语、实验设置和认知负荷 | 作为访谈问题来源；不用于估计普遍比例 |
| U06 | B | [Global GenAI literacy framework](https://www.sciencedirect.com/science/article/pii/S2666920X26000159) | 两项本科生研究，强调知识、误解、信念和负责任使用 | 页面访问受限，只采用摘要层结论，正式引用需取得全文 |

## 3. 商业产品

| ID | 等级 | 来源 | 已公开能力/数据 | 不能据此声称 |
|---|---|---|---|---|
| P01 | C | [NotebookLM Help](https://support.google.com/notebooklm/answer/16164461?hl=en) | PDF/网页/视频/音频/Docs/Slides；源内回答、行内引用、学习指南、简报、音频和思维导图 | 不能证明所有答案准确，也不能证明其评测器已做元验证 |
| P02 | C | [SciSpace](https://scispace.com/) | Chat PDF、Literature Review、AI Writer、抽取和引用等产品模块 | 官方功能列表不是独立质量评测 |
| P03 | C | [Elicit Pricing](https://elicit.com/pricing) | 搜索 1.38 亿以上论文；系统综述最多筛选 5,000 篇；Basic 免费、Plus 11 美元/月年付、Pro 39 美元/月年付 | 价格和配额会变；不能据此比较真实准确率 |
| P04 | C | [Elicit API](https://docs.elicit.com/) | 搜索、系统综述、结构化报告和多格式导出 | API 计划和权限可能变化 |
| P05 | C | [Consensus Subscription Plans](https://help.consensus.app/en/articles/10087865-subscription-plans) | Free、Pro 20 美元/月或 144 美元/年；Pro/Deep 搜索和 Study Snapshot | 不能证明用户留存或评测效果 |
| P06 | C | [Scite](https://scite.ai/) | 2.8 亿以上来源、16 亿以上 Smart Citations、支持/反对分类和原文上下文 | 公司公开规模不等于去重论文数或准确率；分类仍可能出错 |
| P07 | C | [Scholarcy](https://www.scholarcy.com/) | Flashcards、读者层级、表格图像、引用、比较和导出 | 用户数和证言不是受控实验 |
| P08 | C | [ResearchRabbit Features](https://www.researchrabbit.ai/features) | 论文、作者、概念关系图，集合和推荐 | 不解决单篇生成内容的事实忠实度 |
| P09 | C | [Connected Papers About](https://www.connectedpapers.com/about) | 每图分析约 50,000 篇候选；基于共引和文献耦合；S2 数据 | 相似关系不等于支持关系或事实正确性 |

## 4. 学术评测与系统论文

| ID | 等级 | 来源 | 关键数据/方法 | PaperLens 用法 |
|---|---|---|---|---|
| A01 | B | [ResearchQA](https://arxiv.org/abs/2607.11074) | 6,211 QA、494 OA 论文、8 领域、4 问题类型；引用指标区分度高于压缩的 LLM judge 分 | 外部迁移测试、不可回答和引用指标 |
| A02 | B | [QASPER](https://aclanthology.org/2021.naacl-main.365/) | 5,049 问题、1,585 篇 NLP 论文、人工答案和支持证据 | 跨章节证据绑定测试 |
| A03 | B | [SciFact](https://aclanthology.org/2020.emnlp-main.609/) | 约 1.4K 专家主张、支持/反对证据和 rationale | 主张支持分类 sanity check |
| A04 | B | [ALCE](https://arxiv.org/abs/2305.14627) | 流畅、正确、citation quality；precision/recall；ELI5 最佳模型约 50% 支持不完整 | 直接借鉴引文指标 |
| A05 | B | [FActScore](https://aclanthology.org/2023.emnlp-main.741/) | 原子事实支持比例 | `AtomicClaim` 设计 |
| A06 | B | [RefChecker](https://arxiv.org/abs/2405.14486) | 11K claim triplets、2.1K 回答；较其他粒度提升 6.8 至 26.1 点 | 主张抽取、支持/反对/不足 |
| A07 | B | [RAGChecker](https://arxiv.org/abs/2408.08067) | claim-level 诊断检索器和生成器；含元评测 | 错误归因和 meta-evaluation |
| A08 | B | [G-Eval](https://aclanthology.org/2023.emnlp-main.153/) | rubric + CoT + form filling；总结 Spearman 0.514；提示自偏好 | Hy3 rubric 设计与限制说明 |
| A09 | B | [Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685) | 位置、篇幅、自我增强和推理限制；强裁判与人类偏好可达 80% 以上一致 | 交换顺序、重复、人工校准 |
| A10 | B | [Large Language Models are not Fair Evaluators](https://arxiv.org/abs/2305.17926) | 位置偏差；多证据、平衡位置和人工介入 | 成对评测设计 |
| A11 | B | [LLM Evaluators Recognize and Favor Their Own Generations](https://arxiv.org/abs/2404.13076) | 自识别和自偏好风险 | 生成与评测盲化、规则和人工基线 |
| A12 | B | [OpenScholar, Nature](https://www.nature.com/articles/s41586-025-10072-4) | 45M OA 论文、236M 段落嵌入；ScholarQABench 2,967 问题、208 长答案；自反馈循环 | 检索后定向修订，承认不能自动化科研综合 |
| A13 | B | [PaperQA2](https://arxiv.org/abs/2409.13740) | 文献检索、总结、矛盾检测和 LitQA2 人机比较 | Agentic RAG 结构参考，不复制大规模系统 |
| A14 | B | [STORM](https://aclanthology.org/2024.naacl-long.347/) | 结构组织绝对提升 25%，覆盖提升 10%；也有来源偏差转移 | 先证据后大纲，关注来源偏差 |

## 5. 数据、版权和伦理

| ID | 等级 | 来源 | 关键规则 | PaperLens 动作 |
|---|---|---|---|---|
| D01 | A | [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/) | 只有 OA 子集按许可允许再利用；PMC 并非全部可挖掘 | 只选许可明确子集，保存 license 字段 |
| D02 | A | [Crossref REST API](https://support.crossref.org/hc/en-us/articles/214320426-REST-API) | 大多数书目元数据可自由使用，部分摘要可能受版权保护 | 用于 DOI/作者/许可元数据，不默认缓存摘要全文 |
| D03 | A | [Semantic Scholar API License](https://www.semanticscholar.org/product/api/license) | 需同时遵守 API、S2 数据和第三方内容许可 | 不把 API 可访问视为全文可再发布 |
| D04 | A | [Unpaywall Data Format](https://unpaywall.org/data-format) | OA location 明确版本和许可，license 可能为 null | 用于寻找开放版本，null 时不进入公开集 |
| D05 | A | [ICMJE AI Recommendations](https://www.icmje.org/recommendations/browse/artificial-intelligence/) | 人类负责准确、引用、许可和抄袭；AI 不作作者；披露使用；保护保密稿件 | 加 AI 使用说明、拒绝保密稿件、保留人工责任 |
| D06 | A | [UNESCO GenAI Guidance](https://www.unesco.org/en/articles/guidance-generative-ai-education-and-research) | 隐私、知识产权、人工主体性、伦理和教学验证 | 产品定位为辅助，不替代学习和审查 |
| D07 | A | [AI 生成合成内容标识办法](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm) | 2025-09-01 起，涉及文本显式/隐式标识和传播声明 | 导出保留 AI 说明；公开运营前做专项合规评估 |
| D08 | A | [生成式人工智能服务管理暂行办法](https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm) | 向境内公众提供生成式 AI 服务的基本规范 | 比赛本地 demo 与公众服务边界分开说明 |

## 6. 开源项目调研

| ID | 等级 | 来源 | 内容 | 局限 |
|---|---|---|---|---|
| G01 | D | [本地 GitHub 全景评估](./github_landscape_assessment.md) | 31 个代表项目，覆盖学术应用、Deep Research、评测、PDF/OCR | 是技术全景，不是全网穷尽，也不替代用户和市场研究 |
| G02 | D | [OpenPaper eval 文档](https://github.com/khoj-ai/openpaper/blob/master/server/evals/paper.md) | ResearchQA 的确定性引用匹配、rubric 和局限说明 | GitHub 文档可能先于正式论文更新 |
| G03 | D | [RAGChecker repo](https://github.com/amazon-science/RAGChecker) | 评测输入格式、指标和 benchmark 工具 | 依赖外部模型与英文 NLP 组件 |

## 7. 关键冲突与解释

1. 商业产品常用“grounded”或“verifiable”描述功能，但论文显示“有引用”不等于“引用完整且支持主张”。报告采用论文中的 citation precision/recall 和确定性匹配来约束产品宣传语言。
2. LLM-as-a-judge 研究既报告较高人类一致性，也报告位置、篇幅和自偏好。两者不冲突：LLM 裁判可用，但必须在具体任务上校准，不能自动视为真值。
3. TokenHub 条款承诺不把输入用于腾讯自身模型训练，同时允许为推理、审核、运维、计费和合规处理并保留必要数据。因此只能写“不会用于自身模型训练”，不能写“不会被处理或记录”。
4. 开放访问、API 可访问和允许公开再分发是三件不同的事。评测集必须逐篇保存许可，不能只按平台名称推断。

## 8. 当前证据状态

**可直接用于方案 V0.1：** 任务匹配、用户问题假设、竞品能力、学术方法、Hy3 接入与价格、评测设计、合规策略、实施计划。

**仍未完成：** 本地访谈、TokenHub 真机调用、真实 PDF 解析对比、人工双标注、完整实验和 2 分钟 demo。

正式提交前更新本台账的核验日期，并移除无法再次访问或无法确认的来源。
