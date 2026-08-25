# PaperLens 分阶段新会话提示词

## 使用方法

- 阶段 0 已于 2026-08-22 完成并验收，不需要重新开会话。
- 从下文复制对应阶段的完整代码块，作为新会话的第一条消息。
- 一个“阶段会话”可以包含多轮对话和多个原子任务，但只能处理该阶段，不得提前进入下一阶段。
- 外部安装、API Key、付费调用或真实数据授权需要用户操作时，仍留在同一阶段会话中解决。
- 只有全部通过阶段验收后才能结束该会话；阻塞时必须写明未完成，不能降低门槛或用 Mock 冒充 Live。
- 每个阶段的最终报告必须提供一段可直接粘贴运行的完整 PowerShell 验收脚本：使用 `Set-Location -LiteralPath 'D:\Hy3'`、显式调用 `.venv313\Scripts\python.exe`、使用 `npm.cmd`、检查 `$LASTEXITCODE`，并用 `Push-Location/Pop-Location` 恢复目录。不得使用 CMD 的 `cd /d`，不得省略工作目录，不得包含 `<id>`、`...` 等占位符。

## 阶段 1：PDF 解析

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 1“PDF 解析”，通过全部验收后停止，不进入阶段 2。

项目定位：PaperLens 是基于 Hy3 的可信学术解读、主张级证据审计与受约束修订工具。核心是“解析、生成、核验、审计、修改、复核、回退和评测”闭环。工程必须简单、可测、可解释，不增加微服务、队列、向量数据库、多 Agent 或计划外功能。请使用中文，以适合初学者的方式说明当前用户流程和每一步的验证结果。

已知基线：阶段 0 已完成；Python 使用 D:\Hy3\.venv313，后端 19 项测试通过；前端 Vite 7.3.6 的类型检查、生产构建和 npm audit 已通过；MinerU 尚未安装。不要把这些历史结论直接当成当前事实，先做低成本复核。

开始时必须：
1. 将工作目录设为 D:\Hy3。
2. 阅读 AGENTS.md、docs/DEV_PLAN.md 的第 2、3、5、7、8.1、9、10、12 节，以及现有 models.py、settings.py 和相关测试。
3. 运行 `.venv313\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`，确认阶段 0 未回归。
4. 检查当前 Python、`./.venv-mineru312/Scripts/mineru.exe`、pdfplumber 版本和实际文件状态。涉及 MinerU 当前安装方式和 CLI 参数时，先查官方最新文档，不凭记忆猜版本。
5. 先给出阶段任务清单；随后逐个完成原子任务。除非需要用户安装软件、确认外部决策或提供权限，不要只给方案而不实施。

每次编辑前必须先输出：本次唯一目标、最多 4 个允许修改文件、禁止修改范围、固定输入输出与错误码、精确验证命令。先补失败测试，再写最小实现；完成一个原子任务后先跑针对性测试，再跑阶段回归测试。

阶段允许范围：document_service.py、settings.py、test_document_service.py 和短小的 PDF 夹具。不得修改 Hy3、审计、数据库、API 或前端。不得新增业务目录。MinerU 按独立 CLI 工具处理，不加入应用运行依赖。

必须完成：
- 核验 MinerU 官方当前安装要求并安装、锁定实际版本。若 Python 3.13 不兼容，不污染应用虚拟环境；先提出最小隔离方案和准确命令，再让我执行或确认。
- 用参数数组调用 MinerU CLI，不拼接 shell 字符串。
- 每次解析使用独立临时目录，设置超时，捕获退出码和脱敏 stderr 摘要。
- 把 MinerU 输出转换成 SourceBlock[]，统一 page_index、reading_order 和可空 bbox。
- 实现文本型 PDF 的 pdfplumber 明确降级，parser 必须标为 pdfplumber。
- 输出 ParseQuality：页数、块数、空页率、异常字符率、页码完整率和 bbox 可用率。
- 创建并人工核对 simple_2page.pdf；夹具必须短小，不含真实私密论文。

必须覆盖：两页页码映射、MinerU 正常输出、MinerU 不可执行时降级、损坏 PDF 的 PDF_INVALID、无文本扫描件未启用 OCR 时的 PARSE_QUALITY_LOW、成功和失败后的临时目录清理。失败不能伪装成功。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
$MinerU = (Resolve-Path ".\.venv-mineru312\Scripts\mineru.exe").Path
& $MinerU -v
if ($LASTEXITCODE -ne 0) { throw "MinerU version check failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests/test_document_service.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 1 focused tests failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 1 regression tests failed: $LASTEXITCODE" }

阶段完成条件：文本型夹具页码全部正确；每个失败路径都有稳定错误码；MinerU 版本和可用性有真实记录；没有计划外依赖、目录、接口或 Schema 变更。若 MinerU 外部安装仍未成功，本阶段不得标记完成。

结束时只提交阶段报告：完成内容、修改文件、实际版本、测试命令与结果、失败路径覆盖、未解决问题、是否达到阶段门槛。报告必须附上根据最终实现核对过的完整 PowerShell 验收脚本，不得有占位符。停在阶段边界，等待我开启阶段 2 新会话。
```

## 阶段 2：Hy3 联合生成

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 2“Hy3 联合生成”，通过全部验收后停止，不进入阶段 3。

项目定位：PaperLens 是基于 Hy3 的可信学术解读、主张级证据审计与受约束修订工具。工程必须保持扁平、简单、可测；不得增加多 Agent、向量数据库、队列、微服务或计划外功能。Hy3 只能生成五区解读、候选主张、语义判断和修订建议，不能成为页码、bbox、已验证引文、分数或合格结论的事实来源。请使用中文并清楚区分 Mock、Live、设计完成和真实验证。

已知工程基线：阶段 1 已完成；MinerU 使用 `./.venv-mineru312/Scripts/mineru.exe`；应用 Python 依赖由根目录 `requirements.lock` 锁定。不要把这些结论直接当成当前事实，先检查工作区、CLI、锁文件和测试。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 2、4、5、6、7、8.2、9、10、12 节，以及现有 models.py、settings.py、document_service.py 和测试。
2. 复核阶段 1 的代码和测试；运行 `.venv313\Scripts\python.exe -m pip check`，确认 `requirements.lock` 与 `.venv313\Scripts\python.exe -m pip freeze --exclude-editable` 一致，并运行 `.venv313\Scripts\python.exe -m pytest backend/tests/test_document_service.py backend/tests/test_models.py -q -p no:cacheprovider`。未通过则先停止并说明，不能在阶段 2 偷修无关模块。
3. 检查 `.env` 和 HY3_API_KEY 是否存在时只能判断“已配置/未配置”，绝不打印、记录或提交 Key。
4. TokenHub 和 Hy3 接口可能变化，先查腾讯云官方当前文档，核对 base URL、模型 ID、/v1/models、json_schema 和请求参数，并在报告中给出官方来源。
5. 先列出阶段任务，再逐个完成原子任务；每次编辑前输出唯一目标、最多 4 个文件、禁止范围、固定契约/错误码和验证命令。

阶段允许范围：prompts.py、hy3_service.py、settings.py、test_hy3_service.py。不得接前端，不得修改审计、数据库或 API，不得创建第二套模型协议。只有 hy3_service.py 可以调用 Hy3。

必须完成：
- 先实现显式 Mock 模式，并让 Mock 与 Live 经过同一 GeneratedBundle Pydantic 校验链。
- 按 DEV_PLAN 第 6 章实现联合生成 Prompt 和必要模板，结构化输出使用严格 JSON Schema，所有对象 additionalProperties=false。
- 使用 OpenAI Python SDK 兼容客户端；Key 只从后端环境变量读取。
- Schema 失败最多重试 2 次，重试只附加字段错误摘要；第 3 次失败返回 SCHEMA_INVALID。
- Live 缺 Key 返回 HY3_CONFIG_MISSING；Live 网络/供应商失败保持失败，绝不回退 Mock。
- 日志只记录模型 ID、Prompt 版本、参数、token、延迟、重试和错误码，不记录 Key 或大段论文原文。
- 执行真实探针：/v1/models 确认 hy3 在线；用 2 至 3 个 SourceBlock 发起最小 json_schema 请求；用 GeneratedBundle 验证；保存脱敏结构摘要。若需要我配置 Key，给出不回显 Key 的准确操作并在同一会话等待。

必须覆盖：合法 Mock、非 JSON、缺字段、额外字段、受限重试、连续失败、候选 block 尚未核验的状态、日志脱敏、Live 缺 Key。不得把不存在的 candidate_block_id 标为已验证证据。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
$env:PAPERLENS_MODEL_MODE = "mock"
try {
    & $Python -m pytest "backend/tests/test_hy3_service.py" -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Stage 2 focused tests failed: $LASTEXITCODE" }
    & $Python -m pytest "backend/tests/test_models.py" "backend/tests/test_hy3_service.py" -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Stage 2 contract regression failed: $LASTEXITCODE" }
    & $Python -m pytest "backend/tests" -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Stage 2 backend regression failed: $LASTEXITCODE" }
}
finally {
    Remove-Item Env:PAPERLENS_MODEL_MODE -ErrorAction SilentlyContinue
}
# 阶段报告还必须根据最终 Hy3Service 公开调用方式给出真实 Live 探针的完整 PowerShell 指令；不得打印 HY3_API_KEY，不得为验收创建临时生产脚本。

阶段完成条件：Mock 全绿；至少一次真实 Hy3 返回合法 GeneratedBundle 的脱敏运行记录；Key 和论文原文未泄露；没有改动前端或创建计划外依赖/接口。真实探针未成功时不得声称阶段完成。

结束时只提交阶段报告：实现行为、修改文件、Mock 与 Live 的证据、真实模型/Prompt/Schema 版本、测试结果、安全检查、未解决问题、是否通过门槛。报告必须附上完整且无占位符的 Mock 回归和 Live 探针 PowerShell 脚本。停在阶段边界，等待阶段 3 新会话。
```

## 阶段 3：证据核验、快速检查和评分

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 3“证据核验、快速检查和评分”，通过全部验收后停止，不进入阶段 4。阶段 3 已在提交 `be5adf3` 通过独立验收；本段保留为契约与回归事实，后续阶段不得顺手返工。

项目定位：PaperLens 的关键差异不是普通论文总结，而是把解读拆成可核验主张，以 PDF 解析结果为证据源，通过确定性规则和 Hy3 语义裁判完成八维审计。代码而不是 Hy3 负责证据有效性、页码、分数、权重、硬失败和双门槛结论。工程保持扁平，不增加向量数据库、多 Agent 或复杂检索架构。请使用中文，并先解释用户可观察行为再讲实现文件。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 1、2、5、6、7、8.3、9、10、12 节，以及 proposal 中评分、八维和双门槛的有效技术定义。
2. 阅读 models.py、prompts.py、hy3_service.py、audit_service.py、现有夹具和测试，运行阶段 2 回归：`.venv313\Scripts\python.exe -m pytest backend/tests/test_models.py backend/tests/test_hy3_service.py -q -p no:cacheprovider`。
3. 先给出阶段任务清单；每个普通原子任务编辑前输出唯一目标、最多 4 个文件、禁止范围、输入输出/错误码和测试命令。经用户明确授权的深审 v2 原子迁移是唯一八文件例外，必须一次同步切换且不得扩展范围。
4. 先测试失败样例，再写最小实现；规则层和语义层必须可分别测试。

阶段允许范围：常规任务为 audit_service.py、test_audit_service.py；只有契约确实缺失时才补 models.py。深审 v2 已冻结为单一 `DeepAuditResult` 原子替换；经用户单独授权后，可同步修改 models.py、prompts.py、hy3_service.py、audit_service.py、deep_audit_valid.json、test_models.py、test_hy3_service.py 和 test_audit_service.py。不得保留并行 v1/v2 生产入口，不得修改前端、API、数据库，不得增加嵌入模型或向量库。

深审 v2 固定契约：
- 唯一输出为 `DeepAuditResult {semantic_judgments, risk_findings}`，Prompt/Schema/名称版本固定为 `audit-v2`、`deep-audit-result-v2`、`paperlens_deep_audit_result_v2`。
- `ComplianceContext` 由代码提供，包含处理权限或许可确认、来源说明、AI 辅助披露、生成内容标识适用性与存在状态；Hy3 不得判断这些字段。
- Hy3 读取完整 `ContentDraft`，对 `sensitive_information/author_impersonation/academic_integrity` 各返回一条 `RiskFinding`；状态只允许 `detected/not_detected/unclear`。
- `RiskLocation` 固定为 `{location_type, sentence_id, evidence_excerpt}`：`location_type` 只允许 `sentence/title/document`，摘录为 `null` 或最长 160 字符；sentence 必须引用真实生成句，title/document 的 sentence_id 必须为 `null`，document 的摘录必须为 `null`，非空摘录必须在对应标题或句子中规范化匹配。
- `RiskFinding` 固定为 `{category, status, locations, reason, remediation}`。`detected` 至少一个合法位置，`not_detected` 的位置为空，`unclear` 可为空；同类风险可保存多个位置。敏感信息位置的摘录必须为 `null`；结构核验后，无条件把供应商的敏感类别 reason/remediation 替换为按 status 选择的代码固定文本，禁止用格式启发式决定是否脱敏。所有位置只来自完整 `ContentDraft`，不得使用页码、bbox 或 SourceBlock 引文。
- Hy3 不得返回最终风险等级、pass/fail、分数、权重、硬失败、decision、页码或 bbox。代码核验语义配对、三类风险完整性、所有位置和摘录，合并 `ComplianceContext` 后生成 `RiskAssessment {compliance_context, risk_findings, level_points}`，再计算 0 至 4 级、硬失败、5% 权重、核心门槛和最终结论。
- `AuditReport.risk_assessment` 固定为 `RiskAssessment|null`：`quick_complete` 必须无维度、无风险评估、无硬失败且保持 pending；`deep_complete` 必须非空，且 `level_points` 必须与风险合规维度一致。RiskAssessment/AuditReport 在反序列化时复算风险等级、维度展示、风险硬失败、总分、核心门槛和 decision。阶段 4 API 返回并由 `audits.report_json` 保存完整 `AuditReport`，位置、代码脱敏后的敏感类别说明及其他类别理由/修复建议不得丢失。
- 非 JSON、对象缺字段、额外字段或非法枚举为 `SCHEMA_INVALID`；结构合法但 ComplianceContext、配对、风险类别、位置、摘录、检查结果或 RiskAssessment 不完整为 `AUDIT_INCOMPLETE`，且不得生成分数；Live 供应商失败为 `HY3_UNAVAILABLE`，禁止回退 Mock。
- `non_auditable` 内容不进入事实支持率分母，但仍随完整文档接受风险检查。
- 风险映射固定为：全通过=4；一项来源/AI 提示缺失=3；两项提示缺失或任一 `unclear`=2；处理权限或许可未确认=1；必要标识缺失或任一三类风险 `detected`=0。必要标识单独缺失不进入 hard_failures，没有其他硬失败时 decision 必须为 `needs_revision`；三类 `detected` 才分别生成 `SENSITIVE_INFORMATION`、`AUTHOR_IMPERSONATION`、`ACADEMIC_INTEGRITY`。通用 SemanticJudgment.severity 不参与风险合规。

必须完成：
- 核验候选 source_block_id 是否存在；模型候选只是一条线索，不是证据事实。
- 对引文执行 Unicode、空白、换行和断词规范化匹配，保留可追溯的匹配方法。
- 候选无效时使用 rank-bm25 加数字、单位、否定词精确约束召回 Top-3。切片前先规范化断词换行，支持小数、e.g.、Fig. 2、Dr. Smith、短词和单位句边界；使用原子片段与有界相邻窗口，同一 block 最多一个候选。
- 实现页码、引文、数字、单位、否定词、比较方向和五区必需内容检查。
- 实现条件拆分触发器，只拆真正包含多个可独立核验事实的句子，不递归调用所有句子。
- 批量调用 Hy3Service.deep_audit 获取 DeepAuditResult v2；由代码检查完整性、合并 ComplianceContext、生成 RiskAssessment，并计算八维分数、权重、总分、硬失败和双门槛。
- quick_complete 时总分和合格状态必须为 null/pending_deep_audit；只有八维结果齐全才计算完整结论。

必须覆盖：正确 block/引文、假 block、假引用、数字改变、单位改变、否定反转、换行/连字符规范化、无证据 insufficient、快速与完整状态、权重严格为 1、硬失败不可被高分抵消；还要覆盖纯术语 severity 不扣风险分、non_auditable 风险检查、三类风险完整性、RiskLocation 三种位置约束、同类多位置、摘录规范化核验、权限门禁发生在 Hy3 调用前、任意供应商敏感自由文本无条件替换且普通学术文本不误报、4/3/2/1/0 映射、标识缺失无硬失败且为 needs_revision、三类 detected 硬失败、quick/deep RiskAssessment 状态、等级一致及 AuditReport JSON 往返。测试不能只断言总分，还要断言问题定位、原始指标、错误码、调用次数、返回对象和状态。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests/test_models.py" "backend/tests/test_hy3_service.py" "backend/tests/test_audit_service.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 3 focused contract and audit tests failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 3 backend regression failed: $LASTEXITCODE" }

阶段完成条件：黄金主张和证据样例全部符合预期；假引用和数字篡改稳定检出；DeepAuditResult v2 的严格 Schema、语义配对、三类风险检查、完整位置模型和敏感信息脱敏全绿；RiskAssessment 完整持久化且等级与风险合规维度一致；标识缺失与三类 detected 的硬失败映射、评分公式、权重和双门槛测试全绿；Hy3 未直接控制风险等级、最终分数或 pass/fail；无计划外目录、依赖、API 或 Schema。

结束时只提交阶段报告：规则层、语义层、评分层完成情况，修改文件，关键测试样例，测试结果，残余风险，是否通过门槛。报告必须附上完整且无占位符的 PowerShell 验收脚本。停在阶段边界，等待阶段 4 新会话。
```

## 阶段 4：存储和 API 闭环

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 4“存储和 API 闭环”，通过全部验收后停止，不进入阶段 5。

项目定位：PaperLens 必须用最小后端完成上传、解析、联合生成、快速检查、完整审计和状态读取闭环。数据层只使用 sqlite3 与 JSON 快照，不引入 ORM、Redis、队列、后台任务或微服务。API 保持同步且路径严格遵守 DEV_PLAN 4.2。请用中文说明用户流程，并清楚区分已实现、Mock 端到端和真实 Hy3 能力。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 2、3、4、5.5、5.6、5.8、7、8.4、9、10、12 节。
2. 确认 Git 工作区干净、历史包含阶段 3 验收提交 `be5adf3`，阅读 document_service.py、hy3_service.py、audit_service.py、models.py 和现有测试。阶段 3 的 DeepAuditResult v2、风险脱敏、权限门禁和评分验证器均是受保护基线。
3. 运行阶段 3 后端回归：`.venv313\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`。可信起点为 `208 passed, 1 skipped`；数字可能随阶段 4 新测试增加，但任何旧测试失败都必须停止定位，禁止降低或删除旧测试。
4. 先按 DEV_PLAN 8.4 的固定顺序列出六个原子任务。每次编辑最多 4 个文件；编辑前输出唯一目标、允许文件、禁止范围、固定输入输出/错误码和验证命令。先补公开入口或存储边界红测，再写最小实现。

阶段整体允许范围：models.py、project_store.py、api.py、main.py、test_models.py、test_project_store.py、test_api.py；每个原子任务仍遵守最多 4 文件。models.py/test_models.py 只允许第一个契约任务新增 DEV_PLAN 5.8 模型，不得改变现有 Stage 1-3 字段、枚举、评分权重、门槛或验证器。不得修改前端、prompts.py、hy3_service.py、audit_service.py、document_service.py、settings.py、夹具、依赖或锁文件。不得新增 API 路径、数据库表、ORM、后台任务或业务目录。只有 project_store.py 可执行 SQL；api.py 只做请求校验、服务编排和错误映射；main.py 只负责依赖装配与 FastAPI 生命周期。

固定阶段拆分：
1. models.py + test_models.py：DEV_PLAN 5.8 存储/API 契约。
2. project_store.py + test_project_store.py：五表、事务、JSON 双向校验和恢复。
3. api.py + main.py + test_api.py：上传、项目读取、PDF 读取和可注入测试依赖。
4. api.py + test_api.py：生成、自动快速检查、版本/证据/快速报告原子保存。
5. api.py + test_api.py：完整审计、权限重建、报告持久化和错误映射。
6. 以 test_api.py 为主：Mock 核心闭环、失败恢复和最终 PowerShell 冒烟；只有真实红测证明生产缺陷时才修改对应已授权生产文件。

必须完成：
- 使用 sqlite3 建立 projects、versions、audits、patches、runs 五张固定表；只增加已获授权的 projects.rights_confirmed 和 audits.evidence_json，不增加第六张表或其他列。
- 数据库只保存经 DEV_PLAN 5.8 Pydantic 模型验证的 JSON 快照，不保存 pickle，不做通用实体系统；读取时再次验证，损坏或不一致不得静默忽略。
- 阶段 4 只实现 POST /api/projects、GET /api/projects/{id}、GET /api/projects/{id}/pdf、POST /api/projects/{id}/generate、POST /api/projects/{id}/audit。修订、补丁接受、恢复和导出留到阶段 6；不得创建占位路由、501 或固定空响应。
- 上传固定为 multipart file + rights_confirmed。权限未确认必须在读取/保存 PDF 前返回 RIGHTS_NOT_CONFIRMED，不创建项目、PDF 或 run。文件大小有界读取，客户端文件名不参与存储路径。
- 生成不接收客户端 SourceBlock；只读 ParseSnapshot。Hy3Service.generate 成功后先提交初始版本并推进到 generated；随后 AuditService.quick_check 以独立事务保存 EvidenceSnapshot 和 quick AuditReport 并推进到 quick_checked。快速检查失败必须保留已成功生成的稳定版本。
- 审计请求只包含四个披露/标识字段；rights 从 projects.rights_confirmed 重建。只使用当前版本已保存的文档、主张和 EvidenceRecord，保存并返回同一份完整、已脱敏 AuditReport。
- 项目状态遵守 created -> parsed -> generated -> quick_checked -> deep_audited；patch_pending 留到阶段 6。失败保留 error_code，并从最新失败 runs.metadata_json 返回安全消息和 retryable_stage。
- 每阶段成功后提交事务；失败写脱敏 run 记录，但不破坏上一稳定版本、证据或审计。失败不能在数据库或 API 中伪装为 Mock 成功。
- Mock 必须显式启用；Live 失败不能返回 Mock 成功。
- 用命令行或 TestClient 完成上传、生成、快速检查、完整审计和读取当前项目的核心闭环。

必须覆盖：未确认权限不读写、非 PDF、超限文件、有界读取、安全路径、五表精确集合、两列授权 Schema、全部 JSON 写前/读后校验、事务提交与回滚、完整 Mock API 流程、重复读取幂等、失败后旧版本/证据/审计可读、不存在项目/PDF 的稳定错误码、阶段错误、供应商失败、SCHEMA_INVALID、AUDIT_INCOMPLETE、API 与数据库不含 Key/Prompt/原始响应/原文和供应商敏感自由文本。测试必须经过 ProjectStore 或 FastAPI 公开入口，并断言状态、HTTP、错误码、数据库副作用和旧快照。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests/test_models.py" "backend/tests/test_project_store.py" "backend/tests/test_api.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 4 contract/API/store tests failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 4 backend regression failed: $LASTEXITCODE" }
# 阶段报告还必须按 DEV_PLAN 5.8 的真实字段提供完整 PowerShell API 冒烟脚本：使用显式 Mock 环境与独立临时 PAPERLENS_DATA_DIR，启动后端、等待 /api/health、上传 simple_2page.pdf、生成、深度审计、读取项目和 PDF，并在 finally 中只关闭它启动的进程、删除本次临时数据。所有 ID 必须从响应自动提取；不得打印完整响应中的论文文本、Prompt、风险理由或任何 Key。

阶段完成条件：后端全测通过；Mock 模式可从命令行完成五个核心路由闭环；数据库只有五张计划内表及两列明确授权扩展；JSON 往返、权限与证据快照、API 模型和 HTTP 错误映射符合 DEV_PLAN 4.2/4.3/5.8；失败不破坏稳定版本；没有修订占位路由、前端或计划外架构改动。

结束时只提交阶段报告：API 闭环演示结果、数据库表、修改文件、测试数量与结果、失败恢复证据、未解决问题、是否通过门槛。报告必须附上完整且无占位符的测试与 API 冒烟 PowerShell 脚本。停在阶段边界，等待阶段 5 新会话。
```

## 阶段 5：三栏工作台和证据跳转

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 5“三栏工作台和证据跳转”，通过全部验收后停止，不进入阶段 6。

项目定位：第一屏必须是可操作的学术工作台，不做营销落地页。桌面端采用 PDF、解读文档、证据/审计三栏；移动端允许在三个面板间切换。界面要安静、紧凑、适合长时间阅读，不使用装饰性大卡片、渐变球或过度动画。点击解读句子后，最多两次操作到达正确 PDF 页并看到证据摘录。请使用中文，先说明用户操作路径，再实施。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 1、2、3、4、5、7、8.5、9、10、12 节，以及现有前后端 API 契约。
2. 运行阶段 4 回归：`.venv313\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`，并运行 `cd frontend && npm run typecheck && npm run build`。受限环境出现 spawn EPERM 时先诊断，不能把环境限制误报为代码错误；最终仍需在正常环境取得真实构建结果。
3. 阶段 4 的正式门槛已通过，但 `DEV_PLAN` 中的 `LIVE-BLOCKER-01` 仍未关闭：真实 MinerU 和真实 Hy3 生成成功，`claims/evidence=0/0`，risk-only 深审经过三次真实响应后仍为 `502/AUDIT_INCOMPLETE`，边界为 `HY3_RISK_CATEGORY_COVERAGE_INVALID`，且无 Mock fallback。本阶段不得修改后端或顺手修复该问题；必须把空 claims/evidence、完整审计失败、保留快速检查结果和可重试状态作为正式 UI 场景。阶段 5 可以完成，但 `LIVE-BLOCKER-01` 未关闭时不得进入阶段 6。
4. 检查 package.json。已知阶段 3 基线中 `test` 脚本引用 `vitest --run`，但 devDependencies/package-lock 尚无 Vitest 且没有测试文件；这是明确延后到阶段 5 的 P2。第一项前端任务必须安装并锁定实际兼容的 pdfjs-dist、lucide-react、Vitest、Testing Library 和 Playwright，并先建立至少一个真实失败测试；禁止使用 `--passWithNoTests`、删除 test 脚本或依赖全局 CLI 伪装通过。只允许这些 DEV_PLAN 2.3 列出的依赖，安装前核对官方当前兼容版本和许可证，真实安装后锁定。不得添加 UI 框架、状态管理库或第二套请求库，不要用会隐式下载未知版本的 npx 掩盖缺失依赖。
5. 先列阶段任务和组件职责；每个原子任务最多修改 4 个文件，编辑前输出唯一目标、允许文件、禁止范围、固定 API/类型和验证命令。先写测试再实现。

阶段允许范围：frontend/src、前端测试，以及上述已明确批准的 package.json/package-lock.json 依赖变更。不得改变后端 API、Schema、数据库或错误码。

必须完成：
- App.tsx 只管理项目级状态、加载流程和三栏/移动面板布局。
- PdfPane.tsx 负责 PDF 加载、页码、缩放、跳页和基于 PDF.js 公开能力的文本查找；不依赖内部 findController。
- DocumentPane.tsx 按 sentence_id 渲染当前五区文档并处理句子选择。
- SidePanel.tsx 根据状态展示证据、快速检查、深度审计入口、修改入口占位状态和版本历史读取状态。
- api.ts 集中封装全部请求、取消和统一错误映射；组件中不得拼 API URL。
- 明确显示未上传、解析中、生成中、快速检查完成、深度审计中、完整审计完成、证据不足和失败可重试状态。
- `claims=[]`、`evidence_records=[]` 时明确显示证据不足，不创建证据跳转；`AUDIT_INCOMPLETE` 时保留快速检查结果并显示完整审计失败和重试入口。
- 深度审计未完成时不显示总分或合格状态。
- 文本查找失败时仍停留正确页并显示证据摘录；不能白屏。
- 页眉始终显示 Mock 或 Live。

测试与视觉验收必须覆盖：点击句子设置目标页和证据；API 错误和重试；前三个预设流程；桌面 1440x900、移动 390x844；文字、按钮和面板无重叠；最长错误文案不溢出；移动端可切换三面板。用 Playwright 截图并实际查看，不只检查 DOM。PDF 画布还要确认非空且页码正确。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 5 backend regression failed: $LASTEXITCODE" }
Push-Location -LiteralPath "D:\Hy3\frontend"
try {
    & npm.cmd run test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Stage 5 frontend tests failed: $LASTEXITCODE" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Stage 5 typecheck failed: $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Stage 5 production build failed: $LASTEXITCODE" }
    & ".\node_modules\.bin\playwright.cmd" test
    if ($LASTEXITCODE -ne 0) { throw "Stage 5 Playwright tests failed: $LASTEXITCODE" }
    & npm.cmd audit
    if ($LASTEXITCODE -ne 0) { throw "Stage 5 npm audit failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}

阶段完成条件：前三个预设任务 Playwright 全绿；生产构建通过；两个规定视口无重叠；证据跳转、空证据状态和 `AUDIT_INCOMPLETE` 降级路径可见；后端契约未改变；依赖仅限批准清单且 audit 无高危漏洞。阶段报告必须继续列出 `LIVE-BLOCKER-01`；该阻塞不否定阶段 5 的前端验收，但在关闭前 `STAGE_6_ENTRY=HOLD`。

完成后启动本地后端和前端，给我可点击 URL，并提交阶段报告：用户流程、修改文件、依赖变更、测试结果、截图核验、已知限制、是否通过门槛。报告必须附上完整且无占位符的 PowerShell 验收脚本，并负责安全启动、等待和关闭测试服务。停在阶段边界，等待阶段 6 新会话。
```

## 阶段 6：修订、版本和回退

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 6“修订、版本和回退”，通过全部验收后停止，不进入阶段 7。

项目定位：用户应像修改 AI 生成 PPT 的单个元素或整页一样，查看“初稿、审计、修订、复核”过程，与 Hy3 讨论后生成句子级或全文补丁。任何修改都必须先预览，再由用户接受或拒绝；AI 不得无确认覆盖当前文档。接受后生成不可变新版本并重新检查，历史版本可回退。工程保持简单，不增加协同编辑器、字符级富文本或复杂事件溯源框架。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 1、2、4、5、6、7、8.6、9、10、12 节，以及阶段 5 的前后端实现和测试。
2. 首先核对 `DEV_PLAN` 中 `LIVE-BLOCKER-01` 的关闭证据。必须同时存在真实 risk-only `200/deep_complete/8 dimensions` 和至少一条非零 claims、已验证 evidence、semantic judgments 的真实完整闭环，且均无 Mock fallback。证据不足时立即停止，报告 `STAGE_6_ENTRY=HOLD`，要求另开“阶段 4 Live 收口”会话；不得在本阶段顺手修改深审 Prompt、Schema、重试或生成契约。
3. 运行后端全测和前端测试/构建，确认阶段 5 基线真实通过。若 Playwright 需要服务，按项目 README 启动并在结束时清理进程。
4. 先给出多个原子任务及依赖顺序；每次编辑最多 4 个文件，编辑前输出唯一目标、允许文件、禁止范围、固定 EditPatch/API/错误码和验证命令。
5. 不新增依赖。先写拒绝、过期和越界测试，再实现成功路径。

阶段允许范围：hy3_service.py、project_store.py、api.py、SidePanel.tsx 和直接相关测试。按原子任务拆分，不能一次修改所有文件。除契约有已证实矛盾外不得修改 models.py、types.ts 或固定 API。

必须完成：
- 句子修改只提交目标 sentence_id、当前文本、相关证据和用户意图；不得把全文和历史版本一起发给模型。
- 全文修改只提交当前五区内容，不提交历史版本。
- Hy3 只返回 EditPatch 预览，不直接写数据库当前版本。
- 接受补丁前检查 base_version_id、before_hash、目标范围和句子存在性。
- 拒绝补丁不改变当前版本；接受补丁创建不可变新版本，保留父版本，并自动运行快速检查。
- 全文修改后重新生成主张，旧深度审计标为过期。
- 回退通过复制历史快照创建新的当前版本，不移动、不覆盖、不删除历史记录。
- 实现阶段 4 明确延后的 Markdown 导出路由；导出只读取当前稳定版本，包含源论文说明、AI 辅助说明、生成时间和模型信息，不创建第二种内容格式。
- UI 可讨论修改意图、查看前后差异、接受/拒绝、查看版本历史并回退；所有写操作有等待、失败和重试状态。

必须覆盖：合法句子补丁预览/拒绝/接受、越界补丁、过期 hash、不存在句子、拒绝后版本不变、接受后新旧版本并存、句子修改不影响其他句子、全文修改重建主张和审计过期、回退后内容/主张/审计快照一致、Markdown 导出内容与当前版本一致、无确认不覆盖。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests/test_hy3_service.py" "backend/tests/test_project_store.py" "backend/tests/test_api.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 6 focused backend tests failed: $LASTEXITCODE" }
& $Python -m pytest "backend/tests" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 6 backend regression failed: $LASTEXITCODE" }
Push-Location -LiteralPath "D:\Hy3\frontend"
try {
    & npm.cmd run test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Stage 6 frontend tests failed: $LASTEXITCODE" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Stage 6 typecheck failed: $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Stage 6 production build failed: $LASTEXITCODE" }
    & ".\node_modules\.bin\playwright.cmd" test
    if ($LASTEXITCODE -ne 0) { throw "Stage 6 Playwright tests failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}

阶段完成条件：五个预设流程全部通过；不存在自动覆盖；并发/过期目标有稳定错误码；版本和回退语义正确；前后端测试全绿；无计划外依赖或功能。

完成后启动应用并提供可点击 URL，提交阶段报告：句子与全文修订流程、版本语义、修改文件、测试结果、真实可见效果、残余风险、是否通过门槛。报告必须附上完整且无占位符的 PowerShell 验收脚本，并负责安全启动、等待和关闭测试服务。停在阶段边界，等待阶段 7 新会话。
```

## 阶段 7：评测脚本和结果报告

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 7“评测脚本和结果报告”，通过全部验收后停止，不进入阶段 8。阶段 7 可以在同一会话内分多轮运行，不能因耗时较长而降低最终规模。

项目定位：评测必须证明 PaperLens 能识别主张和证据问题、稳定执行八维审计，并在受约束修订后解决已发现问题而不引入严重新错误。报告数字必须由原始 JSONL 重建，不能手工填写。项目是个人项目，不依赖双人独立标注或访谈；要诚实报告单人标注、固定参考答案和重复运行的限制。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、DEV_PLAN 第 1、2、5、7、8.7、9、10、12 节，以及方案书中的数据、评分、实验和时间规划部分。
2. 运行阶段 6 后端和前端关键回归，确认核心闭环未损坏。
3. 检查 eval、reports、数据许可、Hy3 Live 配置和预计调用规模。数据和 API 状态可能变化，使用论文/数据前核对官方许可；只使用公开许可明确的文本，不提交私密论文或许可不明全文。
4. 在任何可能产生明显 Token 费用的 calibrate/final/stability Live 批量运行前，先计算样例数、请求数、断点续跑状态和粗略成本，向我报告并等待确认；已完成的 case_id + run_index 不得重复收费。
5. 先列原子任务；每次编辑前输出唯一目标、最多 4 个文件、禁止范围、评测输入输出/版本字段和验证命令。先测断点续跑、失败记录和报告重建，再跑真实批量。

阶段允许范围：eval、reports，以及评测确实需要的只读服务接口。不得修改评分门槛来迎合结果，不得改生产行为，不得添加评测专用后门或硬编码夹具答案。

必须完成：
- run_eval.py 只有 smoke、calibrate、final、stability 四种模式；build_report.py 从 JSONL 生成报告。
- 先完成冒烟集：1 篇论文的好/中/差 3 份输出；数字篡改、假引用 2 类攻击及配对干净版本；2 份固定输出各运行 2 次。
- 最终不得用冒烟代替：开发集 5 篇 x 3 档共 15 份；独立保留集 5 篇 x 3 档共 15 份；8 类攻击各 2 个共 16 个并包含 16 个配对干净版本；12 份固定输出各运行 3 次共 36 次稳定性评估。
- 评估修订前后错误解决率、新增严重错误和无关内容变更率。
- 支持按 case_id + run_index 断点续跑；失败、超时、不支持样例也写入结果。
- 每条结果包含模型、Prompt、Schema、数据和代码版本以及模式，且不含 Key 和长篇受限全文。
- 最终保留集开始前冻结配置；运行后不得根据结果移动权重、区间或双门槛。
- 报告完整记录样本选择、单人标注限制、失败案例和不确定性，不把相关性当作绝对有效性证明。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "eval/test_eval.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 7 evaluation tests failed: $LASTEXITCODE" }
& $Python "eval/run_eval.py" --mode smoke
if ($LASTEXITCODE -ne 0) { throw "Stage 7 smoke evaluation failed: $LASTEXITCODE" }
& $Python "eval/build_report.py" --input "reports/smoke_results.jsonl"
if ($LASTEXITCODE -ne 0) { throw "Stage 7 smoke report failed: $LASTEXITCODE" }
# 获得费用确认后，再按同样的错误检查顺序执行 calibrate、冻结配置、final、stability 和最终 build_report。阶段报告必须使用真实产物路径生成完整 PowerShell 脚本。
& $Python -m pytest "backend/tests" "eval/test_eval.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Stage 7 full regression failed: $LASTEXITCODE" }

阶段完成条件：冒烟和规定的最终规模均有原始结果；可续跑且不重复收费；失败样例不丢失；报告可完全由 JSONL 重建；冻结后未移动门槛；数据许可和隐私检查通过。未完成 Live 最终运行时不得把阶段标记完成。

结束时提交阶段报告：数据规模、运行模式、真实调用数与失败数、版本冻结信息、核心指标、报告路径、复现命令、费用和限制、是否达到门槛。报告必须附上根据真实产物路径生成的完整、可续跑、无占位符 PowerShell 脚本。停在阶段边界，等待阶段 8 新会话。
```

## 阶段 8：发布检查

```text
继续开发 D:\Hy3 的 PaperLens 项目。本会话只完成 DEV_PLAN 阶段 8“发布检查”。这是最终交付会话，不新增产品功能，只修复发布验收发现的明确问题。

项目定位：最终交付必须是一个工程简单、可复现、可演示的可信学术解读闭环。P0 包括上传、解析、联合生成、证据核验、快速/完整审计、页码与摘录、句子/全文修订、补丁确认、版本回退、Markdown 导出、最终评测和失败案例。P1 未完成可以明确说明；P2 bbox 精确覆盖和复杂 OCR 可以不完成。不得把 Mock 冒充真实 Hy3，不得为了演示隐藏错误或删除失败测试。

开始时必须：
1. 将工作目录设为 D:\Hy3，阅读 AGENTS.md、完整 DEV_PLAN、README 和最终方案书的交付/风险部分。
2. 检查 git 状态并保护用户已有修改；不要重置、回退或删除不属于本阶段的工作。
3. 列出发布验收清单和允许修改的发布文件。只允许修改 README、LICENSE、第三方归属/AI 说明、环境示例、演示材料，以及修复明确验收缺陷所需的最少源文件和测试。每个修复仍遵守最多 4 个文件、先测试后实现。
4. 新建临时干净环境复现安装；不得依赖本机未记录的全局包。涉及当前许可证或官方安装方式时查官方来源，不凭旧记忆。

必须完成：
- 从全新 Python/Node 环境按 README 安装并启动，逐条修正不真实或缺失的命令。
- 运行后端全测、eval 测试、前端单测、类型检查、生产构建、Playwright 和评测 smoke。
- 扫描 Git 跟踪内容及待交付产物中的 API Key、私密 PDF、绝对本机路径、日志原文和许可不明全文；扫描结果不得回显密钥内容。
- 核对 .env.example 仅含 DEV_PLAN 允许变量；补齐 LICENSE、MinerU/PDF.js/Hy3 等第三方归属、AI 使用说明、数据与论文许可说明。
- 核对 Mock/Live 标识、已知限制、P1/P2 状态和失败案例，所有说法必须与真实实现一致。
- 完成不超过两分钟的演示：上传授权 PDF、解析、生成、快速检查、深度审计、点击句子看页码/摘录、提出修订、预览并接受、复核、查看历史并回退、导出。优先录制真实 Hy3；若只能用预置结果，画面和说明必须明确标注。
- 保存最终测试命令、执行日期、环境版本、结果和已知限制。不得只写“测试通过”而没有真实输出摘要。

最终验收使用以下完整 PowerShell 指令：
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "D:\Hy3"
$Python = (Resolve-Path ".\.venv313\Scripts\python.exe").Path
& $Python -m pytest "backend/tests" "eval/test_eval.py" -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Final Python tests failed: $LASTEXITCODE" }
& $Python "eval/run_eval.py" --mode smoke
if ($LASTEXITCODE -ne 0) { throw "Final smoke evaluation failed: $LASTEXITCODE" }
Push-Location -LiteralPath "D:\Hy3\frontend"
try {
    & npm.cmd run test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Final frontend tests failed: $LASTEXITCODE" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Final typecheck failed: $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Final production build failed: $LASTEXITCODE" }
    & ".\node_modules\.bin\playwright.cmd" test
    if ($LASTEXITCODE -ne 0) { throw "Final Playwright tests failed: $LASTEXITCODE" }
    & npm.cmd audit
    if ($LASTEXITCODE -ne 0) { throw "Final npm audit failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
# 阶段报告还必须提供三段根据最终仓库实际生成的完整 PowerShell 脚本：全新环境安装/启动、密钥与私密全文扫描、两分钟演示启动。不得包含绝对临时路径、明文密钥或任何占位符。

视觉验收：在 1440x900 和 390x844 实际查看 Playwright 截图；确认 PDF 非空、三面板无重叠、长文本不溢出、状态和错误可见、核心流程可完成。不要只看测试代码或 DOM 数量。

最终完成条件：所有 P0 通过；P1 未完成项明确；仓库无密钥和私密全文；README 可从零复现；许可证与归属齐全；演示不伪装 Mock；最终报告和原始评测结果对应。任何一项失败都保持“未完成”，修复后重跑受影响测试及完整回归。

结束时提交最终交付报告：实现范围、P0/P1/P2 状态、测试矩阵与实际结果、安全与许可证检查、评测和演示文件路径、启动方式、已知限制。不要再提出或实现范围外的新功能。
```
