# PaperLens 任务一：评估方法、实验结果与能力边界

整理日期：2026-09-10。对应《犀牛鸟开源－实战任务－混元大语言模型项目》PDF 第 1–2 页的**任务一：开放式场景 AI 应用与评判标准设计**。本作品是个人 / 活动项目，非腾讯官方产品。PDF 允许两个任务任选其一，本仓库不以任务二作为交付目标。

**结论：已留存实验支持已查看固定样本上的 `PASS_FIXED_SAMPLE`。** 本页把评测材料、完整结果和分析集中到公开仓库；数据来自此前获准执行的 Hy3 实验，本次整理只做离线重建。小样本、单作者标签和已有开发反馈限制了结论，不能据此声称盲测、普遍可靠或生产就绪。

## 1. 提交材料索引

| PDF 要求 | 仓库中的交付 |
|---|---|
| 应用源码、介绍、环境与运行说明 | [README](../README.md)、[后端](../backend/app/)、[前端](../frontend/src/)、[配置样例](../.env.example)、[Python 锁](../requirements.lock)、[Node 锁](../frontend/package-lock.json) |
| 场景、方案、评估维度及其依据 | 本页第 2–3 节；[项目方案](paperlens_project_proposal.md)、[评分依据研究说明](scoring_basis_research.md) |
| 样本来源、构造和难例 | [10 篇材料与变异清单](../eval/live_cases.json)，本页第 4 节 |
| 评测脚本与方法 | [执行脚本](../eval/run_eval.py)、[报告重建脚本](../eval/build_report.py)，本页第 3–5 节 |
| 完整结果表 | [103 槽逐行结果表](../reports/stage7_full_results_auditv8_scope_r1.md)、[正式汇总报告](../reports/stage7_report_auditv8_scope_r1.md)、[校准报告](../reports/calibrate_report_auditv8_scope_r1_postfreeze.md) |
| 有效性验证的实验过程和数据 | [校准 15 条 JSONL](../reports/calibrate_results_auditv8_scope_r1.jsonl)、[正式 88 条 JSONL](../reports/stage7_results_auditv8_scope_r1.jsonl)、[对应冻结](../reports/stage7_frozen_auditv8_scope_r1.json)，本页第 5–6 节 |
| 典型 case、失败模式、能力边界 | 本页第 7–8 节；[阶段 7 收尾](stage7_closeout.md) |
| 2 分钟以内 demo | [50.04 秒视频](../reports/stage8_demo.webm)，真实本地 API + pdfplumber，模型明确为 Mock 预置结果；演练步骤见 README |

材料齐备不等于主办方已评分或认可效果。视频演示与 Hy3 固定样本实验分别提供交互证据和模型评测证据。

## 2. 场景选择与应用方案

目标用户是需要阅读跨专业论文的学生、研究人员和技术读者。需求包括理解研究问题、方法与样本、核心发现、结论限制，并能回到原文核查。解读不存在唯一正确措辞；单看流畅性或一个总分，难以发现数字、比较方向、因果和适用人群被改写的问题。

大模型用于将来源材料组织成五个阅读区域、拆出可核验主张、判断证据与语义关系，以及按用户意图提出修订。规则程序负责来源块定位、引文存在性、数字 / 单位 / 否定 / 比较方向检查、分数和门槛。这样的分工使语义判断可用，同时避免让模型自行声称“原文第几页”或自行批准自己的分数。

应用的闭环为：上传并确认处理权限 → 解析 → 生成解读和原子主张 → 查看证据与快速规则检查 → 完整审计 → 预览句子 / 全文修订 → 接受或拒绝 → 复核 → 回退和导出。实现职责见 [模型契约](../backend/app/models.py)、[来源解析](../backend/app/document_service.py)、[Hy3 边界](../backend/app/hy3_service.py)和[审计评分](../backend/app/audit_service.py)。只有 Hy3Service 调用 Hy3；模型调用失败保持失败。完整审计缺失时不产生完整评分。

当前部署与工程验收范围是 Windows 本地单用户源码交付。默认 Mock 仅支持明确的合成演练，不是任意论文解读器；真实 Hy3 使用方式在 README 单独说明。

## 3. 八维操作化标准与设计依据

采用“规则核验 + 结构化 Hy3 语义审计 + 代码聚合”的半自动方法。事实与证据项权重较高，是因为论文解读首先要保持研究内容；表达项用于检查可读性，但不能抵消严重事实错误。引用准确性检查已有引用是否成立，完整性检查关键主张是否得到覆盖，两者分别计算。研究对象、因果和限制单列，避免只核对词句相似度。

研究依据及其来源在 [评分依据研究说明](scoring_basis_research.md)和[项目方案第 6 节](paperlens_project_proposal.md#6-八维评估与双门槛)。下表按本轮实际评分代码整理。**具体权重、分档与通过阈值是本项目的工程选择，来源文献并未认证这些数值。**

每维为 0–4 级；下表“比例档”按所列阈值依次给 4/3/2/1 级，低于最后阈值为 0。比例分母为 0 时按 0% 处理，不能凭缺少证据得满分。

| 维度（冻结权重 / 最低级别） | 可操作判定 |
|---|---|
| 事实一致性（20% / 3） | 被支持主张的加权比例；关键主张权重 2、一般主张 1。比例档 95/85/70/50%。1 条关键矛盾至多 1 级，至少 2 条为 0；关键证据不足至多 2 级。 |
| 引用准确性（15% / 3） | 原候选引用需引文核验成功、语义支持且无确定性矛盾；准确候选对 / 全部候选对。比例档 95/85/70/50%。1 个伪造引用标记至多 1 级，至少 2 个为 0。 |
| 引用完整性（15% / 3） | 被支持且具有已验证证据的关键主张 / 全部关键主张。比例档 90/80/60/30%。 |
| 方法范围（15% / 3） | 方法、结果、结论类主张中，证据支持且范围保持的比例。比例档 90/75/50/25%；出现重大或关键范围扩张至多 1 级。 |
| 结论与限制（15% / 3） | 结果、结论、限制类主张中，得到支持且范围保持的比例。比例档 90/75/50/25%；缺少限制主张为 0，存在无支持限制至多 2 级。 |
| 术语（7% / 2） | 无问题为 4；1 个非重大问题为 3；多个非重大问题为 2；1 个重大问题为 1；关键错误或至少 2 个重大问题为 0。存在可核验主张但没有语义判断时为 0。 |
| 读者适配（8% / 2） | 检查五区齐全、术语至少 3 级且未检出未解释术语、句长合规且未检出冗余离题、关键主张有核验引文、语义判断均保持范围。通过 5/4/3/1–2/0 项分别为 4/3/2/1/0 级。 |
| 风险合规（5% / 3） | 从 4 级开始；缺 1 项来源 / AI 披露为 3，缺 2 项至多 2；风险不明确至多 2；未确认处理权利至多 1；必需内容标记缺失或检出风险为 0。检出风险同时按固定规则产生硬失败。此项是应用审计规则，不是法律合规认证。 |

总分为 `25 × Σ(维度级别 × 冻结权重)`，四舍五入到两位小数。存在硬失败则 `unqualified`；无硬失败、各维最低门槛全部达到且总分 ≥75 才是 `qualified`；其余完整审计为 `needs_revision`。源契约在 `models.py::compute_audit_outcome`，并非由 Hy3 直接输出总分或通过结论。

文档级表达审计必须覆盖 `redundancy_or_off_topic` 与 `unexplained_terminology`，且检出位置能绑定正文句子。缺项或不确定状态不能伪装成完整审计。本轮使用 `audit-v8`、`deep-audit-result-v3`、`paperlens-stage7-method-v3`；修订和主张重建分别使用 `revision-v3` 与 `sentence-claims-v3`。

## 4. 样本来源、构造与难例覆盖

[样本清单](../eval/live_cases.json)含 10 篇 PLOS 论文的短篇评测用证据陈述，不包含论文全文。每篇记录 DOI、官方来源链接、CC BY 许可及其核查链接；许可记录日期为 2026-09-02，归属说明见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。项目代码的 MIT 许可不替代论文自身许可。

选题覆盖社交媒体和心理、游戏训练、饮酒行为、社会关系、睡眠、认知表现、疾病观察、健康素养及物种估计。development 与 holdout 各 5 篇；每篇构造 good / medium / bad 三档固定解读。medium 和 bad 通过清单中的定点变异制造遗漏、方向、数字、范围等已知问题，两档合计占质量样本 20/30。标签由一名项目作者提供，无独立双人标注或评审者一致率数据。

对抗部分含 8 类各 2 对：增加篇幅、堆砌术语、伪造引用、数字 / 单位扰动、相关改因果、扩大适用范围、删除限制、评分提示注入。每对同时保留 clean 和 attack，共 32 个文档槽位；不按运行后得分挑选对照。

稳定性预先指定 12 份固定输出：8 份质量输出和 4 份攻击输出，每份 `run_index=0,1,2` 各评一次。修订使用 5 篇 holdout 的 bad 版本，每槽依次执行修订前审计、修订、主张重建、修订后审计。

清单中 `untouched holdout` 描述最初分组意图。数据后来已经过开发查看和反馈，本轮仅作为**已查看的固定样本复验**；不能把该历史字段解释为当前盲测。多个变体与重复评分也不能算成多篇独立论文。清单的来源块和 `page_index` 是构造的评测坐标，不是原始 PDF 页码。

## 5. 实验过程、冻结与可追溯性

本轮沿用评测代码提交 `b625ec75a9fffbde430ed4fdba3db938e818a096`，运行内容指纹为：

`workspace-content-35623d9b0da0b4ad7c1573b12a9c634980058b7248ad3b44187c14d4091dcbd1`

发布时间的提交不会替换这一实验基线。本次独立核对确认报告构建、样本、评测执行、审计评分、模型契约、提示词和设置文件与该隔离实验副本对应文件一致；阶段 8 已验收的 Mock 演练适配有单独发布历史，不用新发布树的整体指纹冒充旧实验指纹。

| 顺序（记录时间为 UTC） | 逻辑槽位 | 供应商尝试 | 过程 |
|---|---:|---:|---|
| calibrate，2026-09-10 01:06:09–01:07:32 | 15 | 15 | 5 篇 development 各三档；检查质量门槛 |
| 冻结，01:12:50 | — | 0 | 保存模型、方法、权重、门槛、材料与范围关联指纹 |
| final，01:15:14–01:21:25 | 52 | 67 | 15 质量 + 32 对抗 + 5 修订 |
| stability，01:25:26–01:28:49 | 36 | 36 | 12 份固定输出各 3 次 |
| 本次公开材料整理 | 0 | 0 | 原件复制、离线汇总和报告重建 |

本轮合计 103 条结果、118 次已知供应商尝试，状态均为 `succeeded / NONE`；未补跑取优，无失败、重试、超时、中断或未知用量记录需要从本批剔除。这里的成功表示执行完整，不意味着文档全部合格或修订全部解决。更早的历史诊断不并入本轮统计。

正式汇总 JSONL 是原 final 文件字节与 stability 文件字节按顺序拼接，含 88 条、103 次调用；calibrate 独立保存。每行保留 `case_id/run_index/mode/status/error_code`、模型 / Prompt / Schema / 数据 / 代码版本、调用数、时间、Token 和指标。未保存原始供应商回答、完整提示词或修订后全文，因而公开数据能复算已记录的指标，不能还原未留存的逐字模型输出。

正式记录用量为输入 300,899、输出 57,365、合计 358,264 tokens。原报告按当时代码中的每百万输入 1 元、输出 4 元估算为 0.530359 元；calibrate 的原估算为 0.079909 元。此处仅解释历史报告，未查询或宣称当前价格，实际账单未核验。

### 人工范围关联的边界

[reviewed scope manifest](../eval/reviewed_scope_manifest_auditv8_scope_r1.json)仅为 `revision:holdout-04:bad` 的目标句绑定 b01/b02 研究设计及参与者范围与 b03 统计结果；其他槽位不自动获得这一关联。文件摘要是 `e6c86a…1d78`，规范化内容指纹是 `cea141…51b6`，两种摘要对象不同。

[历史人工复核记录](../eval/scope_review_record_auditv8_scope_r1.json)保留创建时的 `PENDING_USER_DIGEST_CONFIRMATION`，没有事后改成 approved；后续明确批准依据为用户交接及[阶段 7 收尾记录](stage7_closeout.md)。历史草稿本身不是批准凭证。哈希核验只证明材料与关联没有变化，不证明人工判断必然正确，也不代表产品支持自动跨块研究关联。

[阶段 7 验收原记录](../reports/stage7_acceptance_auditv8_scope_r1.json)中的 `stage8=NOT_STARTED` 是当时状态；当前阶段 8 结论见[发布说明](stage8_publish.md)。其中历史锁、pending 和预冻结报告摘要作为留存清单保留，它们不属于本次公开运行包，也不能作为恢复收费任务的依据。

## 6. 有效性验证结果

一致性验证选择 PDF 允许的“同一输出重复评估”，未声称与多名人工评审一致。质量排序在每篇的三档分数之间比较，平分不算正确严格排序；三组两两比较均按严格大于判断。Spearman 是描述性统计，不作为额外通过证据。

| 指标 | 校准 | 正式结果 | 冻结门槛 |
|---|---|---|---|
| 好 > 中 > 差严格排序 | 4/5 | 5/5 | 至少 4/5 |
| 三档两两排序 | 14/15 | 15/15 | 至少 13/15 |
| 已知严重错误检出 | 5/5 | 5/5 | ≥90% |
| 全部已知错误检出（描述项） | 9/10 | 10/10 | 非单独冻结门槛 |
| 关键引用准确率 | 55/60 | 54/60＝90% | ≥90% |
| 关键引用完整率 | 55/60 | 54/60＝90% | ≥80% |
| 稳定性平均总分标准差 | 不适用 | 1.1993288533809268 | ≤5 |
| 稳定性维度一致率 | 不适用 | 85/96＝88.54% | ≥80% |
| 攻击检出 / 干净对照误报 | 不适用 | 16/16；0/16 | 至少 13；至多 1 |
| 修订目标问题解决 | 不适用 | 4/5＝80% | ≥70% |
| 修订新增严重错误 | 不适用 | 0 | 0 |
| 非目标内容修改 | 不适用 | 0/5 | ≤5% |

正式冻结门槛均通过，且核对了版本绑定与计划覆盖。稳定性先对每份输出的 3 个分数计算**总体标准差**，再对 12 份取算术平均；维度一致要求某输出某维度的 3 次级别完全相同，分母为 `12×8=96`。原汇总报告中 gate 的 `denominator=36` 是相关运行记录数，不是维度一致率的分母。

关键引用准确率的分母是正式质量行中已记录的关键候选证据条目，分子还要求原候选引文核验、语义支持且无确定性问题；完整率按关键主张计数，要求充分证据并排除相关矛盾。它们是本评测定义下的指标，不是全部文献引用的查全率。具体边界见 `run_eval.py::_live_report_metrics`。

攻击指标在目标规则 / 语义异常、保守的非目标严重警报或指定表达 / 维度条件触发时记为检出；它衡量警报能力，**不是严格的因果归因准确率，也不等于攻击文档必定不合格**。修订“解决”要求目标句实际变化，重建后的目标主张有覆盖且相关证据语义支持、范围保持、无重大 / 关键错误和确定性矛盾；分数上升本身不算解决。见 `_attack_detected` 与 `_revision_metrics`。

完整数值和每槽状态在[逐槽表](../reports/stage7_full_results_auditv8_scope_r1.md)，两份原报告均能从公开 JSONL 离线逐字节重建。

## 7. 典型模式、失败案例与归因限制

| 案例 | 记录中的实际观察 | 可支持的解释与边界 |
|---|---|---|
| 校准 `quality:dev-03:medium` | good 和 medium 均为 98；`comparison_omission` 未检出；目标 c03 仍被判支持且范围保持 | 本轮漏掉了一个比较信息遗漏，导致好中打平。不能因正式集排序 5/5 就称校准没有失败；公开指标不足以判断模型内部为何忽略该信息。 |
| 正式 `quality:holdout-01:good` | 88.5 分，但 `needs_revision`；方法范围仅 2 级。c02 的关系是 supports，范围却是 expanded / minor | 证据语义支持与研究范围保持是不同要求；高总分不会绕过核心维度门槛。参考标签 good 是预设质量档，不是每次模型审计都应自动合格的真值。 |
| 两个 `attack-length-padding` 配对 | clean 100 → attack 98；表达冗余被定位到 s05，读者适配 4→3；攻击仍为 qualified | 能指出轻微表达攻击，但现有权重与门槛容许其通过。检出 16/16 不构成“攻击全部阻断”的证据，本次没有按结果改阈值。 |
| 两个 `attack-terminology` 配对 | clean 98 → attack 91；未解释术语和冗余被检出，读者适配 3→2、风险 4→0，而术语准确性仍为 4 | 文档表达问题与可核验主张的术语准确性落在不同维度，可能同时触发多类警报；不能把所有降分归因于术语维度。 |
| `revision:holdout-02:bad` | 修订前 44.25 → 后 46.25，解决 0/1；重建主张 c03-r1/r2 对 b02 出现 `COMPARISON_DIRECTION_MISMATCH`，关系 insufficient | 提分未消除目标问题。`CANDIDATE_QUOTE_MISSING` 也保留在诊断中，但不能只凭该标记断言整个修订根因；记录没有修订全文，不编造模型原句。 |
| `revision:holdout-04:bad` | 44.25 →100，解决 1/1；本槽使用已复核范围关联 | 在明确给定研究设计和参与者上下文的条件下解决了该目标；不能推导其他论文或普通产品调用也会自动识别跨块关系。 |
| 稳定性 `dev-04:good`、`holdout-01:good` | 分数分别为 90.5/100/90.5 与 98/88.5/88.5；两者极差均为 9.5 | 全局平均标准差较低仍掩盖个别输出的波动；重复评分只支持本配置下的观察性稳定性。 |

五个修订案例全部列示如下。它们有各自的修订前审计，不能把质量排序行的分数当成同一次前测：

| case_id | 前分 | 后分 | 解决数 | 新增严重错误 | 非目标修改 |
|---|---:|---:|---:|---:|---|
| revision:holdout-01:bad | 33.25 | 92.25 | 1/1 | 0 | false |
| revision:holdout-02:bad | 44.25 | 46.25 | 0/1 | 0 | false |
| revision:holdout-03:bad | 35.25 | 100 | 1/1 | 0 | false |
| revision:holdout-04:bad | 44.25 | 100 | 1/1 | 0 | false |
| revision:holdout-05:bad | 35.25 | 98 | 1/1 | 0 | false |

上述归因依据已存主张诊断、维度差异和确定性规则；没有原始模型文本或独立人工复审支持的部分，不能进一步作因果断言。

## 8. 能力边界与尚未验证项

- 本次有效性实验主要评估**固定构造解读的审计与句子修订**，没有系统测量从任意真实 PDF 到自由生成解读的端到端效果。视频中的模型是明确预置结果，不能填补这一缺口。
- 只有 10 篇短证据材料、单作者标签；没有新盲测、多人一致性、跨领域大规模泛化、跨模型对照、消融实验或统计显著性结论。模型同时参与生成与评判存在共同偏差的可能，当前数据不能排除。
- 三档排序是分别审计后由代码比较分数，没有同时向模型呈现左右候选；未测成对裁判的位置偏差。表达攻击仅每类 2 对，不足以估计真实场景的误报 / 漏报率。
- 引文存在、范围关联和哈希绑定各有用途；构造材料上的成功不验证真实 PDF 页码，也不证明来源论文的研究结论正确。复杂 OCR、表格 / 公式和 bbox 精确覆盖没有正式效果验收。
- 未测长期服务可用性、真实用户研究和对外部署；`PRODUCTION_READY=NO`。历史费用为代码估算，非已确认账单。
- 保留漏检、修订未解决、总分波动和攻击仍合格等记录。改进方向可以是补充比较遗漏案例、复核修订中的方向保持、扩大独立样本；这些属于后续研究，当前提交没有执行新实验或调整冻结门槛。

## 9. 字节摘要与零调用复核

以下是复制原件的 SHA256。`.gitattributes` 对两份原始 JSONL 和含原始 CRLF 的验收记录禁用文本换行转换，保证 Windows/Linux 检出后摘要不变；其余原件沿用现有 LF 规则。冻结文件在公开仓库中另名保存，避免覆盖早期 `reports/stage7_frozen_config.json`；该旧文件属于历史实验，**不能用于本轮报告的正式重建**。

| 文件 | SHA256 |
|---|---|
| `eval/live_cases.json` | `9da351a6d8e9bf65e959c1c65be3cb41f33c5486211752085707339b2d00fa7d` |
| `eval/reviewed_scope_manifest_auditv8_scope_r1.json` | `e6c86a6218cd6e4c8adea8daaf7dfdc8de59b890b8c0aa5252414f38031c1d78` |
| `eval/scope_review_record_auditv8_scope_r1.json` | `7e4bfb4026da061f81d33ff29a32d3af5064fdc05929ea56fd0ac4ce5486fde7` |
| `reports/stage7_frozen_auditv8_scope_r1.json` | `64198896fa0ec6398a4b66de5108d0bfc144ca56d0ff1a34eac64de228a8a955` |
| `reports/calibrate_results_auditv8_scope_r1.jsonl` | `c9abcdf9027ce15fde7816929f12bb3840649507fb7ec00a37c931bad9839048` |
| `reports/calibrate_report_auditv8_scope_r1_postfreeze.md` | `5deb3d52a8b2d457825a18209f2570b4d6254013fc9c832d3b4cb9747dd7c0cb` |
| `reports/stage7_results_auditv8_scope_r1.jsonl` | `0e0ed06263cba254fb81d6b13aa4c4158821833caa2236e674b00ee24f37fe30` |
| `reports/stage7_report_auditv8_scope_r1.md` | `9c1c0c16dc7a144ebb6299c5e1cef73490295930d5d401f2cf7d2db97b4dc8d2` |
| `reports/stage7_acceptance_auditv8_scope_r1.json` | `fd8056fc93aaa9e08a64df72eff43a5243c210ece2a11afaff1c5646bd844232` |

按 README 安装锁定依赖后，在仓库根目录运行以下 PowerShell。输出进入新的临时目录；脚本只读已存结果并调用已有报告函数，不调用评测 CLI、不加载仓库 `.env`、不请求供应商，也不重新冻结。选择冻结路径只改变本进程的报告元数据来源，不修改任何仓库文件。

```powershell
$ErrorActionPreference = 'Stop'
$PaperLensRepo = (Get-Location).Path
$PaperLensPython = (Resolve-Path '.\.venv313\Scripts\python.exe').Path
$PaperLensCheck = Join-Path ([System.IO.Path]::GetTempPath()) ('paperlens-report-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $PaperLensCheck | Out-Null
@'
from pathlib import Path
import hashlib, json, os, statistics, sys
from collections import defaultdict
repo, out = (Path(p).resolve() for p in sys.argv[1:3])
os.chdir(out)
def boundary(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("NETWORK_FORBIDDEN")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if Path(os.fsdecode(args[0])).name.lower() == ".env":
            raise RuntimeError("ENV_READ_FORBIDDEN")
sys.addaudithook(boundary)
sys.path.insert(0, str(repo))
import eval.build_report as report
from eval.run_eval import load_mode_cases, _load_reviewed_scope_manifest
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
report.DEFAULT_FREEZE_PATH = repo / "reports/stage7_frozen_auditv8_scope_r1.json"
assert sha(report.DEFAULT_FREEZE_PATH) == "64198896fa0ec6398a4b66de5108d0bfc144ca56d0ff1a34eac64de228a8a955"
frozen = json.loads(report.DEFAULT_FREEZE_PATH.read_text(encoding="utf-8"))
assert sha(repo / "eval/live_cases.json") == frozen["manifest_sha256"]
contexts, binding = _load_reviewed_scope_manifest(
    repo / "eval/reviewed_scope_manifest_auditv8_scope_r1.json",
    "e6c86a6218cd6e4c8adea8daaf7dfdc8de59b890b8c0aa5252414f38031c1d78",
)
assert set(contexts) == {"revision:holdout-04:bad"}
assert binding == frozen["reviewed_scope_sha256"]
batches = [
    ("calibrate_results_auditv8_scope_r1.jsonl", "calibrate_report_auditv8_scope_r1_postfreeze.md",
     "c9abcdf9027ce15fde7816929f12bb3840649507fb7ec00a37c931bad9839048",
     "5deb3d52a8b2d457825a18209f2570b4d6254013fc9c832d3b4cb9747dd7c0cb",
     ("calibrate",), 15, 15),
    ("stage7_results_auditv8_scope_r1.jsonl", "stage7_report_auditv8_scope_r1.md",
     "0e0ed06263cba254fb81d6b13aa4c4158821833caa2236e674b00ee24f37fe30",
     "9c1c0c16dc7a144ebb6299c5e1cef73490295930d5d401f2cf7d2db97b4dc8d2",
     ("final", "stability"), 88, 103),
]
for data, original, data_hash, report_hash, modes, count, calls in batches:
    path = repo / "reports" / data
    assert sha(path) == data_hash
    rows = report.load_results(path)
    planned = {(c.case_id, c.run_index) for mode in modes for c in load_mode_cases(mode)}
    assert len(rows) == count
    assert {(r["case_id"], r["run_index"]) for r in rows} == planned
    assert sum(r["provider_calls"] for r in rows) == calls
    assert all(r["code_version"] == frozen["code_version"] for r in rows)
    summary = report.build_report(input_path=path, output_path=out / original)
    assert sha(out / original) == sha(repo / "reports" / original) == report_hash
    if "stability" in modes:
        assert summary["acceptance_gates"]["status"] == "passed"
        groups = defaultdict(list)
        for row in rows:
            if row["mode"] == "stability":
                groups[row["case_id"]].append(row["metrics"])
        assert len(groups) == 12
        consistent = sum(len({r["dimension_points"][d] for r in group}) == 1
                         for group in groups.values() for d in frozen["dimension_weights"])
        sd = statistics.mean(statistics.pstdev(r["overall_score"] for r in group)
                             for group in groups.values())
        assert consistent == 85 and abs(sd - 1.1993288533809268) < 1e-12
    print(data, "HASH_AND_REPORT=PASS", "rows=", count, "historical_calls=", calls)
raw = (repo / "reports/stage7_results_auditv8_scope_r1.jsonl").read_bytes().splitlines(keepends=True)
for mode, expected in [
    ("final", "fd1f4ff03f6d26f9f5ae2a1a5934a773967a0ee85f13d9d27eeec267c3f03bd0"),
    ("stability", "07c4f26fabfd3efb56723d1315ef8dfbc8dbec573f879eeca1520d31244ce2d8"),
]:
    data = b"".join(line for line in raw if json.loads(line)["mode"] == mode)
    assert hashlib.sha256(data).hexdigest() == expected
    (out / (mode + "_results_auditv8_scope_r1.jsonl")).write_bytes(data)
print("OFFLINE_REBUILD=PASS; NEW_PROVIDER_CALLS=0")
print("Outputs:", out)
'@ | & $PaperLensPython -B - $PaperLensRepo $PaperLensCheck
if ($LASTEXITCODE -ne 0) { throw 'Offline report verification failed' }
```

该命令在本次公开提交整理时实际执行通过：两份报告均逐字节一致；calibrate 15 槽、final 52 槽、stability 的 12×3 槽与清单完全匹配，拆出的 final / stability 数据也与历史摘要一致。103 槽结果表逐行对照 JSONL，新增材料的相对链接与高置信度密钥模式检查通过。

本次未运行新的 Live、真实 MinerU 或业务回归；业务代码和测试未改动，已关闭的阶段 8 工程回归承接[独立发布验收记录](stage8_publish.md)。以上是公开材料与离线可复算性的核查，不是再次开展有效性实验。
