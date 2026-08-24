# PaperLens 全流程开发文档

> 用途：将项目方案转换为个人可执行、可测试、可复现的开发步骤  
> 适用版本：V0.1 至 V1.0  
> 开发周期：2026-08-23 至 2026-09-10  
> 项目交付日期：2026-09-11  
> 状态：执行基线 V1.0

## 0. 如何使用本文档

本文档是开发阶段的唯一执行入口。每次只完成一个任务卡，完成对应测试并满足门槛后，才能进入下一项。

文档优先级如下：

1. `paperlens_final_project_proposal.md`：规定产品目标、课题对齐、评测承诺和最终边界。
2. `DEV_PLAN.md`：规定目录、接口、数据契约、实现顺序和测试方法。
3. `backend/app/models.py`：实现后成为前后端和模型输出的数据契约真源。
4. 自动化测试：规定代码的可观察行为。

发生冲突时不得自行猜测：先停止当前任务，列出冲突位置、影响和最小修改建议。AI 不得自行修改验收数字、API 路径、Schema 字段和最终评测规模。

## 1. 最终要实现什么

### 1.1 必须完成的闭环

1. 用户上传单篇文本型 PDF，并确认拥有处理权限。
2. MinerU 将 PDF 转换为统一的 `SourceBlock[]`；MinerU 不可用时，普通文本 PDF 可降级到 pdfplumber。
3. Hy3 在一次结构化调用中生成五区本科生解读和 `AtomicClaim[]` 候选。
4. 后端核验每条主张的 `source_block_id`、引文、页码、数字和单位。
5. 系统自动运行快速规则检查；用户点击按钮后运行完整 Hy3 语义审计。
6. 三栏工作台展示 PDF、当前文档、证据与审计信息。
7. 点击句子后，可跳转到正确 PDF 页并看到证据摘录；文本匹配成功时高亮原文。
8. 用户可以与 Hy3 讨论并生成句子级或全文 `EditPatch`，预览后接受或拒绝。
9. 接受修改后生成新版本并复核；用户可以回退到历史版本。
10. 评测脚本能运行校准、保留集判别、稳定性、对抗性和修订有效性实验，并从原始 JSONL 生成报告。

### 1.2 优先级

| 优先级 | 能力 | 处理原则 |
|---|---|---|
| P0 | 上传、解析、联合生成、证据核验、快速检查、完整审计 | 缺少任一项都不能称为核心闭环完成 |
| P0 | 页码跳转、证据摘录、句子/全文修改、补丁确认、版本回退、Markdown 导出 | 保证应用真实可操作 |
| P0 | 最终评测脚本、原始结果和失败案例 | 保证评判标准有验证证据 |
| P1 | 文本查找高亮 | P0 稳定后完成 |
| P2 | bbox 精确覆盖、复杂 OCR/表格 PDF 正式支持 | 只有坐标抽检通过且有剩余时间才启用 |

P2 未完成不伪装成已完成，也不影响 P0 验收。页面级定位和证据摘录是正式降级路径，不是错误状态。

### 1.3 明确不实现

- 多论文项目、论文搜索、知识图谱和向量数据库。
- 多 Agent 编排、模型微调、本地部署 Hy3。
- 用户注册、云端多租户、权限角色和在线支付。
- Celery、Redis、消息队列、微服务和 Kubernetes。
- 富文本协同编辑、任意字符级修订和自动无确认改写。
- 第二种内容格式、移动端 App 和浏览器插件。

## 2. 技术和依赖边界

| 部分 | 固定技术 | 禁止或限制 |
|---|---|---|
| 前端 | React、TypeScript、Vite、PDF.js | 不再引入第二套 UI 框架或状态管理框架 |
| 后端 | Python 3.11、FastAPI、Pydantic v2 | 不使用 Django，不拆微服务 |
| 数据 | Python `sqlite3`、JSON/JSONL | 不引入 ORM、PostgreSQL、Redis |
| PDF 主解析 | MinerU `pipeline` | 只经 `DocumentService` 调用，不让业务代码读取 MinerU 原始字段 |
| PDF 降级 | pdfplumber | 只支持普通文本 PDF，必须标记 `parser=pdfplumber` |
| 模型 | OpenAI Python SDK 兼容客户端、TokenHub `hy3` | Key 只在后端；不允许前端调用 |
| 检索 | `rank-bm25`、标准库正则 | 不引入嵌入模型和向量库 |
| 后端测试 | pytest、FastAPI TestClient | 每个服务先测失败路径 |
| 前端测试 | Vitest、Testing Library、Playwright | 不做截图测试平台或复杂测试服务 |

实现前核验资料：

- [MinerU 官方快速开始](https://github.com/opendatalab/MinerU/blob/master/docs/en/quick_start/index.md)：本地 CLI 支持 `mineru -p <input> -o <output> -b pipeline`。安装完成后立即锁定实际版本。
- [TokenHub 调用概览](https://cloud.tencent.com/document/product/1823/130079)：运行前通过 `/v1/models` 确认 `hy3` 在线。
- [TokenHub 结构化输出](https://cloud.tencent.com/document/product/1823/135873)：使用 `json_schema`，所有对象必须设置 `additionalProperties: false`。
- [PDF.js 官方入门](https://mozilla.github.io/pdf.js/getting_started/)：只依赖公开 Display/Viewer 能力；不把内部 `findController` 当稳定接口。

### 2.1 依赖变更规则

新增依赖前必须在任务输出中回答：

1. 标准库或当前依赖为什么不能完成？
2. 新依赖只会在哪个文件中使用？
3. 许可证是否兼容？
4. 删除该依赖的降级路径是什么？

未经确认不得新增依赖。版本只在真实安装和冒烟测试后写入锁文件，不在文档中猜测未来版本号。

### 2.2 固定环境变量

`.env.example` 只允许包含以下变量名和非敏感默认值：

```text
PAPERLENS_ENV=development
PAPERLENS_DATA_DIR=./data
PAPERLENS_MODEL_MODE=mock
MAX_PDF_MB=30
MINERU_COMMAND=./.venv-mineru312/Scripts/mineru.exe
MINERU_BACKEND=pipeline
MINERU_TIMEOUT_SECONDS=300
HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1
HY3_MODEL=hy3
HY3_API_KEY=
HY3_TIMEOUT_SECONDS=120
HY3_MAX_RETRIES=2
```

规则：

- `HY3_API_KEY` 不提供默认值，不进入前端和测试快照。
- `PAPERLENS_MODEL_MODE` 只允许 `mock/live`。
- 最终真实演示必须显式设为 `live`；测试默认显式注入 `mock`。
- `PAPERLENS_DATA_DIR` 由 `settings.py` 解析为绝对路径并验证位于项目数据目录内。
- `MINERU_COMMAND` 默认指向仓库内 `./.venv-mineru312/Scripts/mineru.exe`；相对路径由 `settings.py` 按项目根目录解析。

### 2.3 最小依赖清单

`pyproject.toml` 的运行依赖只包含：

```text
fastapi
uvicorn
pydantic
pydantic-settings
python-multipart
openai
pdfplumber
rank-bm25
```

开发依赖只包含 `pytest` 和 FastAPI TestClient 所需的 `httpx`。MinerU 体积较大，按官方方式单独安装并由 CLI 适配，不作为应用包的普通依赖自动下载。

应用 Python 环境的完整锁定结果保存在根目录 `requirements.lock`，并且只能由 `.venv313\Scripts\python.exe -m pip freeze --exclude-editable` 的实际输出生成。锁文件使用 UTF-8 无 BOM，不得包含 editable 项目、`file:///`、本机绝对路径或任何密钥。MinerU 独立环境不写入该锁文件，其安装版本和重建命令单独记录在 README。

前端运行依赖只包含 `react`、`react-dom`、`pdfjs-dist` 和 `lucide-react`；开发依赖使用 Vite、TypeScript、Vitest、Testing Library 与 Playwright。状态由 React 自带能力管理，不增加 Redux、MobX、Zustand 或第二套请求库。

初始化后必须提交 Python 和 npm 锁定结果。若某个包只是为了一个很短的工具函数而引入，应改用标准库或本地函数。

## 3. 固定项目结构

```text
paperlens/
├─ README.md
├─ LICENSE
├─ .env.example
├─ pyproject.toml
├─ requirements.lock
├─ AGENTS.md
├─ frontend/
│  ├─ package.json
│  ├─ package-lock.json
│  └─ src/
│     ├─ main.tsx
│     ├─ App.tsx
│     ├─ api.ts
│     ├─ types.ts
│     ├─ styles.css
│     └─ components/
│        ├─ PdfPane.tsx
│        ├─ DocumentPane.tsx
│        └─ SidePanel.tsx
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api.py
│  │  ├─ models.py
│  │  ├─ settings.py
│  │  ├─ prompts.py
│  │  ├─ document_service.py
│  │  ├─ hy3_service.py
│  │  ├─ audit_service.py
│  │  └─ project_store.py
│  └─ tests/
│     ├─ fixtures/
│     ├─ test_models.py
│     ├─ test_document_service.py
│     ├─ test_hy3_service.py
│     ├─ test_audit_service.py
│     ├─ test_project_store.py
│     └─ test_api.py
├─ eval/
│  ├─ data/
│  ├─ run_eval.py
│  ├─ build_report.py
│  └─ test_eval.py
├─ reports/
└─ docs/
   ├─ paperlens_final_project_proposal.md
   └─ DEV_PLAN.md
```

结构硬规则：

- 后端业务文件保持扁平，不创建 `services/claims/judge/scoring/retrieval` 等多层目录。
- `models.py` 集中管理 Pydantic 模型和枚举，不在路由中临时定义字典协议。
- `api.py` 只校验请求、调用服务和映射错误，不写评分、解析或 Prompt 逻辑。
- `hy3_service.py` 是唯一允许调用 Hy3 的文件。
- `document_service.py` 是唯一允许读取 MinerU 原始输出的文件。
- `project_store.py` 是唯一允许直接执行 SQL 的文件。
- 单个文件超过约 400 行且包含两个可独立测试的职责时才允许拆分。
- 不创建只有一个函数的包装文件，不为“未来可能使用”预建目录。

## 4. 固定状态流和 API

### 4.1 项目状态

```text
created -> parsed -> generated -> quick_checked -> deep_audited
                                -> patch_pending -> quick_checked/deep_audited
任一阶段 -> failed；修复后只重试失败阶段
```

约束：

- API 采用同步请求，不实现后台队列。
- 每个阶段完成后立即保存数据库事务。
- `failed` 必须包含 `error_code`、可读消息和可重试阶段。
- Mock 响应必须通过显式环境变量启用，并在界面显示 `MOCK`；不得在真实调用失败后静默返回 Mock。

### 4.2 API 清单

| 方法与路径 | 行为 | 成功结果 | 主要失败码 |
|---|---|---|---|
| `POST /api/projects` | 上传并解析 PDF | 项目、解析质量、`SourceBlock` 统计 | `PDF_INVALID`、`PARSE_FAILED`、`RIGHTS_NOT_CONFIRMED` |
| `GET /api/projects/{id}` | 读取完整当前状态 | 当前文档、审计、版本列表 | `PROJECT_NOT_FOUND` |
| `GET /api/projects/{id}/pdf` | 读取本地 PDF | PDF 文件流 | `PDF_NOT_FOUND` |
| `POST /api/projects/{id}/generate` | 联合生成并自动快速检查 | 文档、主张、规则结果 | `HY3_UNAVAILABLE`、`SCHEMA_INVALID` |
| `POST /api/projects/{id}/audit` | 批量运行完整语义审计和评分 | 完整 `AuditReport` | `EVIDENCE_NOT_READY`、`SCHEMA_INVALID`、`AUDIT_INCOMPLETE`、`HY3_UNAVAILABLE` |
| `POST /api/projects/{id}/revisions` | 生成补丁预览，不改当前版本 | `EditPatch` | `TARGET_STALE`、`PATCH_INVALID` |
| `POST /api/projects/{id}/revisions/{patch_id}/accept` | 接受补丁并生成新版本 | 新版本和快速检查结果 | `TARGET_STALE`、`PATCH_INVALID` |
| `POST /api/projects/{id}/versions/{version_id}/restore` | 复制历史版本为新当前版本 | 新版本 | `VERSION_NOT_FOUND` |
| `GET /api/projects/{id}/export` | 导出当前 Markdown 与来源说明 | `.md` 文件 | `PROJECT_NOT_READY` |

不允许在开发过程中创建功能重复的路径。若确需改变 API，必须同时修改本节、`types.ts`、后端契约测试和前端 API 测试。

### 4.3 SQLite 最小表结构

数据库固定为五张表，不建立通用实体系统：

| 表 | 必需字段 |
|---|---|
| `projects` | `id`、`stage`、`pdf_path`、`pdf_sha256`、`parse_json`、`current_version_id`、`error_code`、`created_at`、`updated_at` |
| `versions` | `id`、`project_id`、`version_no`、`parent_version_id`、`content_json`、`claims_json`、`reason`、`created_at` |
| `audits` | `id`、`project_id`、`version_id`、`status`、`report_json`、`created_at` |
| `patches` | `id`、`project_id`、`base_version_id`、`status`、`patch_json`、`created_at` |
| `runs` | `id`、`project_id`、`version_id`、`operation`、`mode`、`status`、`metadata_json`、`usage_json`、`error_code`、`started_at`、`ended_at` |

所有 JSON 列在写入前必须经过 Pydantic 校验。`status/stage` 使用固定枚举；不允许在 SQL 中保存 Python pickle。删除项目时再清理其 PDF 和解析临时文件，普通重试不得删除上一个稳定版本。

## 5. 数据契约模板

以下是实现时必须保持的最小字段。Pydantic 模型均设置 `extra="forbid"`；发送给 Hy3 的每层 JSON Schema 均设置 `additionalProperties: false`。

### 5.1 SourceBlock

```json
{
  "block_id": "p04-b012",
  "page_index": 3,
  "type": "text",
  "text": "原文内容",
  "bbox": [0.12, 0.18, 0.84, 0.31],
  "reading_order": 12,
  "parser": "mineru",
  "parser_version": "locked-version"
}
```

规则：

- `page_index` 从 0 开始；界面显示时加 1。
- `bbox` 为归一化坐标或 `null`，不能填伪造的 `[0,0,1,1]`。
- `block_id` 由后端生成，不接受模型新建。
- `type` 仅允许 `title/text/table/formula/image_caption`。

### 5.2 联合生成响应 GeneratedBundle

```json
{
  "document": {
    "title": "面向本科生的论文解读",
    "sections": [
      {
        "section_id": "research_question",
        "heading": "研究问题",
        "sentences": [
          {
            "sentence_id": "s-001",
            "text": "该研究考察了……"
          }
        ]
      }
    ]
  },
  "claims": [
    {
      "claim_id": "c-001",
      "sentence_id": "s-001",
      "text": "该研究考察了……",
      "claim_type": "background",
      "importance": "critical",
      "qualifiers": ["该研究"],
      "numeric_entities": [],
      "auditability": "auditable",
      "candidate_block_ids": ["p01-b003"],
      "candidate_quote": "与该主张直接相关的原文"
    }
  ]
}
```

固定约束：

- 正文必须包含 `research_question/methods/results/limitations/plain_explanation` 五区且各出现一次。
- 所有 `sentence_id`、`claim_id` 唯一。
- 每条可核验主张必须属于一个存在的句子。
- `candidate_block_ids` 只能引用输入中存在的 ID；后端必须再次校验。
- `candidate_quote` 只是候选，不是已验证证据。
- 禁止模型生成页码、bbox、总分和合格结论。

### 5.3 EvidenceRecord

```json
{
  "claim_id": "c-001",
  "block_id": "p01-b003",
  "page_index": 0,
  "quote": "已从 SourceBlock 核验的原文",
  "bbox": null,
  "match_method": "model_candidate",
  "quote_verified": true,
  "rule_flags": []
}
```

`match_method` 只允许 `model_candidate/bm25_fallback/none`。`page_index` 和 `bbox` 必须从 `SourceBlock` 复制，不能来自模型。

### 5.4 SemanticJudgment

```json
{
  "claim_id": "c-001",
  "block_id": "p01-b003",
  "relation": "supports",
  "scope_status": "preserved",
  "terminology_status": "correct",
  "severity": "none",
  "reason": "证据直接支持该主张，且适用范围未扩大。"
}
```

枚举固定如下：

- `relation`: `supports/contradicts/insufficient`
- `scope_status`: `preserved/expanded/unclear`
- `terminology_status`: `correct/misused/unclear`
- `severity`: `none/minor/major/critical`

`SemanticJudgment` 只描述单个已验证 `claim/evidence` 配对的事实关系、范围和术语判断。`severity` 只修饰该语义问题，不得被解释为风险合规类别或最终风险等级，也不得通过解析 `reason` 推断风险。阶段 3 的深审 v2 迁移完成后，`SemanticJudgment[]` v1 不再作为独立顶层响应；不得并行保留两套生产协议。

### 5.5 深审 v2 风险合规契约

#### 5.5.1 ComplianceContext

`ComplianceContext` 由代码构造并验证，不是 Hy3 输出：

```json
{
  "rights_or_license_confirmed": true,
  "source_disclosure_status": "present",
  "ai_assistance_disclosure_status": "present",
  "generated_content_label_applicability": "not_applicable",
  "generated_content_label_status": "not_applicable"
}
```

固定枚举与约束：

- `rights_or_license_confirmed`: 必填布尔值
- `source_disclosure_status`、`ai_assistance_disclosure_status`: `present/missing`
- `generated_content_label_applicability`: `applicable/not_applicable`
- `generated_content_label_status`: `present/missing/not_applicable`
- `generated_content_label_applicability=not_applicable` 时，`generated_content_label_status` 必须为 `not_applicable`。
- `generated_content_label_applicability=applicable` 时，`generated_content_label_status` 只能为 `present/missing`。
- Hy3 不得推断用户是否拥有处理权限、来源或 AI 辅助说明是否存在、生成内容标识是否适用或存在。

#### 5.5.2 RiskLocation

`RiskLocation` 只定位完整 `ContentDraft` 中的内容，不是 PDF 来源证据：

```json
{
  "location_type": "sentence",
  "sentence_id": "s-001",
  "evidence_excerpt": "该句中可核验的风险片段"
}
```

三个字段全部必填，固定约束如下：

- `location_type`: `sentence/title/document`
- `sentence_id`: `string|null`
- `evidence_excerpt`: `string|null`，非空时最长 160 个字符
- `location_type=sentence` 时，`sentence_id` 必须引用当前完整 `ContentDraft` 中真实存在的句子。
- `location_type=title` 或 `location_type=document` 时，`sentence_id` 必须为 `null`。
- `evidence_excerpt` 非空时，必须能在对应句子或标题中执行 Unicode、空白、换行和断词规范化匹配；不得带入对应位置以外的内容。
- `location_type=document` 只表达无法缩小到标题或单句的文档级判断，`evidence_excerpt` 必须为 `null`。
- 所有位置只能来自完整 `ContentDraft`；不得返回页码、bbox、`SourceBlock` 引文或其他 PDF 位置信息。

#### 5.5.3 RiskFinding

Hy3 只对完整生成文档执行三类语义风险检查，并为每类恰好返回一条 `RiskFinding`：

```json
{
  "category": "author_impersonation",
  "status": "detected",
  "locations": [
    {
      "location_type": "sentence",
      "sentence_id": "s-001",
      "evidence_excerpt": "作者声称亲自完成了该实验"
    }
  ],
  "reason": "该表述可能冒充论文作者身份。",
  "remediation": "改为第三人称的论文解读表述。"
}
```

五个字段全部必填，固定约束如下：

- `category`: `sensitive_information/author_impersonation/academic_integrity`
- `status`: `detected/not_detected/unclear`
- `locations`: `RiskLocation[]`，允许保存同类风险在完整文档中的多个实际位置
- `reason`: 必填，1 至 500 个字符
- `remediation`: 必填，1 至 300 个字符
- 三个 `category` 必须各出现一次，不能缺失、重复或额外增加类别。
- `status=detected` 时 `locations` 至少包含一个合法位置。
- `status=not_detected` 时 `locations` 必须为空数组。
- `status=unclear` 是合法结果，`locations` 可以为空；非空时每个位置仍必须满足 `RiskLocation` 的全部核验规则。
- `category=sensitive_information` 时，每个 `RiskLocation.evidence_excerpt` 必须为 `null`；`reason` 和 `remediation` 不得复述完整敏感值，只能说明风险类型与安全修改方式。

Hy3 不得返回最终风险等级、风险维度分数、权重、硬失败、`core_gate_passed`、`decision`、页码、bbox，也不得判断许可、披露或标识的适用性。

#### 5.5.4 DeepAuditResult v2

现有深审协议从顶层 `SemanticJudgment[]` v1 原子升级为唯一的 `DeepAuditResult` v2：

```json
{
  "semantic_judgments": [
    {
      "claim_id": "c-001",
      "block_id": "p01-b003",
      "relation": "supports",
      "scope_status": "preserved",
      "terminology_status": "correct",
      "severity": "none",
      "reason": "证据直接支持该主张。"
    }
  ],
  "risk_findings": [
    {
      "category": "sensitive_information",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现敏感信息。",
      "remediation": "无需修改。"
    },
    {
      "category": "author_impersonation",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现作者身份冒充。",
      "remediation": "无需修改。"
    },
    {
      "category": "academic_integrity",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现鼓励违反学术诚信的内容。",
      "remediation": "无需修改。"
    }
  ]
}
```

`DeepAuditResult` 顶层只能包含必填的 `semantic_judgments` 和 `risk_findings`。`DeepAuditResult`、`SemanticJudgment`、`RiskFinding`、`RiskLocation` 和嵌套对象全部使用严格 JSON Schema，`additionalProperties=false`。深审 v2 固定使用：

- Prompt 版本：`audit-v2`
- Schema 版本：`deep-audit-result-v2`
- Schema 名称：`paperlens_deep_audit_result_v2`

深审输入同时包含完整 `ContentDraft` 和已验证 `claim/evidence` 配对。完整文档包含 `non_auditable` 句子；它们不进入事实支持率分母，但必须进入三类文档级风险检查。`ComplianceContext` 由 `AuditService` 单独接收和合并，不发送给 Hy3 让其重新判断。

责任与错误边界固定如下：

- Hy3 只返回 `semantic_judgments` 和三类结构化 `risk_findings`。
- 代码核验语义配对、风险类别完整性、所有 `RiskLocation` 和 `evidence_excerpt`，再合并 `ComplianceContext`。
- 非 JSON、对象缺字段、额外字段或非法枚举属于结构错误，经过受限重试后返回 `SCHEMA_INVALID`。
- JSON 结构合法但 `ComplianceContext`、语义配对、风险类别覆盖、位置、摘录、检查结果或代码生成的 `RiskAssessment` 缺失、不完整、重复、额外或无法验证时，`AuditService` 返回 `AUDIT_INCOMPLETE`，不得生成完整八维分数、核心门槛或最终结论。
- Live 供应商调用失败返回 `HY3_UNAVAILABLE`，禁止回退 Mock。

#### 5.5.5 RiskAssessment

`RiskAssessment` 由代码在完整校验 `ComplianceContext` 和 `DeepAuditResult.risk_findings` 后生成，不是 Hy3 输出：

```json
{
  "compliance_context": {
    "rights_or_license_confirmed": true,
    "source_disclosure_status": "present",
    "ai_assistance_disclosure_status": "present",
    "generated_content_label_applicability": "not_applicable",
    "generated_content_label_status": "not_applicable"
  },
  "risk_findings": [
    {
      "category": "sensitive_information",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现敏感信息。",
      "remediation": "无需修改。"
    },
    {
      "category": "author_impersonation",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现作者身份冒充。",
      "remediation": "无需修改。"
    },
    {
      "category": "academic_integrity",
      "status": "not_detected",
      "locations": [],
      "reason": "未发现鼓励违反学术诚信的内容。",
      "remediation": "无需修改。"
    }
  ],
  "level_points": 4
}
```

三个字段全部必填；`risk_findings` 必须保存校验通过的三个类别及其全部位置、理由和修复建议，`level_points` 必须是代码按下表计算的 0 至 4 整数。任何缺失或不一致均为 `AUDIT_INCOMPLETE`。

风险与合规的代码映射按严重信号优先，通用 `SemanticJudgment.severity` 不参与该维度：

| 结构化信号 | 代码风险信号 | 风险等级 | 硬失败 |
|---|---|---:|---|
| 全部操作检查通过；三类风险均 `not_detected` | 无 | 4 | 否 |
| 来源说明或 AI 辅助披露恰好 1 项 `missing` | 1 项提示缺失 | 3 | 否 |
| 来源说明与 AI 辅助披露均 `missing`，或任一风险为 `unclear` | 2 项提示缺失或中风险 | 2 | 否 |
| `rights_or_license_confirmed=false` | 高风险 | 1 | 否；上传/API 仍应优先返回 `RIGHTS_NOT_CONFIRMED` |
| 标识适用但 `generated_content_label_status=missing` | 严重风险 | 0 | 否；单独缺失标识不加入 `hard_failures` |
| 任一三类风险为 `detected` | 严重风险 | 0 | 按类别生成 `SENSITIVE_INFORMATION`、`AUTHOR_IMPERSONATION` 或 `ACADEMIC_INTEGRITY` |

多个信号同时出现时取最低等级；任何 0 级信号优先于其他项。标识适用但缺失会使风险合规维度为 0 级并令核心门槛不通过，但它本身不构成硬失败；没有其他硬失败时最终 `decision=needs_revision`，不得判为 `unqualified`。只有三类风险的 `detected` 状态分别生成 `SENSITIVE_INFORMATION`、`AUTHOR_IMPERSONATION`、`ACADEMIC_INTEGRITY` 硬失败。代码仍按风险与合规权重 5%、核心门槛最低 3 级计算总分和双门槛。快速检查保持 `dimensions=[]`、`overall_score=null`、`core_gate_passed=null`、`decision=pending_deep_audit`。

### 5.6 AuditReport

```json
{
  "audit_status": "deep_complete",
  "dimensions": [
    {
      "dimension_id": "factual_consistency",
      "raw_metrics": {"supported": 8, "auditable": 10},
      "score": 80.0,
      "level": "good"
    }
  ],
  "risk_assessment": {
    "compliance_context": {
      "rights_or_license_confirmed": true,
      "source_disclosure_status": "present",
      "ai_assistance_disclosure_status": "present",
      "generated_content_label_applicability": "not_applicable",
      "generated_content_label_status": "not_applicable"
    },
    "risk_findings": [
      {
        "category": "sensitive_information",
        "status": "not_detected",
        "locations": [],
        "reason": "未发现敏感信息。",
        "remediation": "无需修改。"
      },
      {
        "category": "author_impersonation",
        "status": "not_detected",
        "locations": [],
        "reason": "未发现作者身份冒充。",
        "remediation": "无需修改。"
      },
      {
        "category": "academic_integrity",
        "status": "not_detected",
        "locations": [],
        "reason": "未发现鼓励违反学术诚信的内容。",
        "remediation": "无需修改。"
      }
    ],
    "level_points": 4
  },
  "hard_failures": [],
  "core_gate_passed": true,
  "overall_score": 82.5,
  "decision": "qualified"
}
```

快速检查阶段必须使用：

```json
{
  "audit_status": "quick_complete",
  "dimensions": [],
  "risk_assessment": null,
  "hard_failures": [],
  "core_gate_passed": null,
  "overall_score": null,
  "decision": "pending_deep_audit"
}
```

`AuditReport.risk_assessment` 的类型固定为 `RiskAssessment|null`。`quick_complete` 必须为 `null`；`deep_complete` 必须包含非空 `RiskAssessment`，且其 `level_points` 必须与 `risk_compliance` 维度 `raw_metrics.level_points` 一致。阶段 4 的 API 返回完整 `AuditReport`，`audits.report_json` 保存同一完整对象，因此风险位置、理由和修复建议必须随报告持久化，不能在 API 或存储边界丢失。

完整八维分数、核心门槛和总分只能由 `audit_service.py` 根据规则与语义结果计算，Hy3 不得直接返回。

### 5.7 EditPatch

```json
{
  "patch_id": "patch-001",
  "base_version": 1,
  "scope": "sentence",
  "target_sentence_ids": ["s-001"],
  "before_hash": "sha256-value",
  "before_text": "修改前文本",
  "after_text": "修改后文本",
  "reason": "补充原文中的样本范围限定",
  "fact_changed": false,
  "evidence_changed": false
}
```

约束：

- `scope` 只允许 `sentence/document`。
- 预览阶段不改变数据库当前版本。
- 接受时重新计算 `before_hash`；不一致立即返回 `TARGET_STALE`。
- 句子补丁不能包含目标句子之外的文本。
- 不提供“自动接受”选项。

## 6. Hy3 Prompt 模板

所有 Prompt 保存在 `backend/app/prompts.py`，使用版本常量，例如 `GENERATION_PROMPT_VERSION = "gen-v1"`。不得把 Prompt 分散在路由、测试和前端。

### 6.1 共同系统约束

```text
你是 PaperLens 的受约束学术内容处理模块。
你只能依据输入中的 SourceBlock，不得使用外部知识补充事实。
不得创建输入中不存在的 block_id、页码、引文、数字或研究结论。
证据不足时必须输出 insufficient 或空候选，不得猜测。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。
```

### 6.2 联合生成模板

```text
任务：面向本科生生成一份可核验论文解读，并同步给出每个句子的原子主张候选。

输入：
- paper_metadata: {{paper_metadata_json}}
- source_blocks: {{source_blocks_json}}

正文固定为五区：研究问题、方法与数据、主要结果、限制条件、通俗解释。
每句话必须有稳定 sentence_id。
每条 AtomicClaim 只表达一个可独立判断真假的事实，并关联 sentence_id。
主张中的数字、样本、条件、比较对象和结论范围不得省略。
candidate_block_ids 只能从输入选择，最多 3 个。
candidate_quote 必须复制候选来源块中的连续或仅规范化空白后的文本。
建议、修辞和主观说明标记为 non_auditable。

输出：严格符合 GeneratedBundle JSON Schema。
```

### 6.3 条件拆分模板

只在后端规则判定一句包含多个独立事实时调用：

```text
将目标句拆为最少数量、可独立判断真假的 AtomicClaim。
不得改写句子，不得增加事实，不得改变 candidate_block_ids。
若无法可靠拆分，将原主张标记为 needs_review。

target_sentence: {{sentence_json}}
existing_claims: {{claims_json}}
allowed_source_blocks: {{source_blocks_json}}
```

### 6.4 深度审计模板

```text
任务一：逐条判断 claim 是否被给定 evidence 支持。
不得参考分数、预设质量档位、攻击标签或其他 claim 的最终判断。
重点检查：事实关系、相关性与因果、样本和适用范围、术语语境、关键限定条件。
证据不能直接支持时选择 insufficient，不得依靠常识补足。

任务二：检查完整生成文档中的 sensitive_information、author_impersonation 和 academic_integrity。
每个风险类别恰好返回一条 RiskFinding；用 RiskLocation[] 保存所有实际位置，non_auditable 句子也必须检查。
RiskLocation 只能定位完整 ContentDraft 的 sentence/title/document；摘录最长 160 字符且必须可核验，敏感信息摘录必须为 null。
不得判断许可、来源披露、AI 辅助披露或生成内容标识是否适用和存在。
不得返回风险等级、分数、权重、硬失败、合格结论、页码或 bbox。

document: {{content_draft_json}}
items: {{verified_claim_evidence_pairs_json}}

输出：严格符合 DeepAuditResult v2 JSON Schema 的单个 JSON 对象。
```

### 6.5 修订模板

```text
根据用户意图生成一个待确认 EditPatch，不直接修改文档。
scope=sentence 时只能修改 target_sentence_ids 指向的句子。
style_change 时锁定数字、限定条件、事实关系和证据。
content_change 时必须说明 fact_changed 和 evidence_changed。
不得删除原文明确给出的关键限制，不得生成新引用。

current_version: {{version}}
scope: {{scope}}
target_text: {{target_text}}
verified_evidence: {{evidence_json}}
user_instruction: {{user_instruction}}
```

## 7. Mock 与测试夹具

### 7.1 必备夹具

`backend/tests/fixtures/` 只保留下列固定文件：

| 文件 | 用途 |
|---|---|
| `simple_2page.pdf` | 两页文本型 PDF，包含已知数字和限制句 |
| `source_blocks.json` | `simple_2page.pdf` 的规范化结果 |
| `generation_valid.json` | 合法联合生成响应 |
| `generation_invalid_block.json` | 引用不存在 block 的响应 |
| `generation_invalid_schema.json` | 缺少必填字段的响应 |
| `deep_audit_valid.json` | 合法 `DeepAuditResult` v2 响应，含精确语义配对和三类风险检查 |
| `patch_sentence_valid.json` | 合法句子补丁 |
| `patch_out_of_scope.json` | 修改越界补丁 |

夹具必须短小、人工可读，并包含明确预期。不得把真实长论文全文复制进测试目录。

### 7.2 Mock 规则

- `PAPERLENS_MODEL_MODE=mock` 时，`Hy3Service` 从夹具读取响应。
- `PAPERLENS_MODEL_MODE=live` 时，只调用 TokenHub，失败就返回错误。
- Mock 与真实响应必须经过同一 Pydantic 校验、证据核验和评分代码。
- Mock 不允许在生产默认配置中启用。
- 前端页眉必须显示当前是 `Mock` 还是 `Live`。

### 7.3 真实接口探针

真实 Hy3 接入不能拖到界面完成后。第一个探针只做三件事：

1. 调用 `/v1/models` 确认 `hy3` 在线。
2. 用 2 至 3 个 `SourceBlock` 发起最小 `json_schema` 请求。
3. 用 `GeneratedBundle` Pydantic 模型验证响应并保存脱敏的结构摘要。

探针失败时只修改 `hy3_service.py`、`settings.py` 和相应测试，不得为适配供应商返回临时污染业务模型。

## 8. 分阶段开发任务与测试门槛

### 阶段 0：环境、骨架和契约

**允许修改**：`pyproject.toml`、`.env.example`、前后端骨架、`models.py`、`types.ts`、契约测试。

任务：

1. 检查 `python --version`、`node --version`、`npm --version` 和 `.\.venv-mineru312\Scripts\mineru.exe -v`。
2. 初始化 FastAPI 和 React/Vite，但不写业务界面。
3. 实现第 5 章的 Pydantic 模型和对应 TypeScript 类型。
4. 建立统一错误响应：`{"error_code":"...","message":"...","retryable":false}`。
5. 创建全部 Mock JSON，并让模型契约测试读取它们。

测试：

```powershell
python -m pytest backend/tests/test_models.py -q
cd frontend
npm run build
```

通过门槛：

- 所有合法夹具通过 Pydantic 校验。
- 缺字段、额外字段、非法枚举和重复 ID 均被拒绝。
- TypeScript 构建无错误。
- 未创建计划外目录和依赖。

### 阶段 1：PDF 解析

**允许修改**：`document_service.py`、`settings.py`、`test_document_service.py` 和 PDF 夹具。

任务：

1. 使用参数数组调用 `./.venv-mineru312/Scripts/mineru.exe`，不拼接 shell 字符串。
2. 每次解析使用独立临时目录，设置超时并捕获退出码和 stderr 摘要。
3. 将 MinerU 输出转换为 `SourceBlock[]`，统一页码、阅读顺序和可空 bbox。
4. 实现文本型 PDF 的 pdfplumber 降级。
5. 输出 `ParseQuality`：页数、块数、空页率、异常字符率、页码完整率和 bbox 可用率。

测试：

```powershell
python -m pytest backend/tests/test_document_service.py -q
```

必须覆盖：

- 两页 PDF 的文本分别落在正确 `page_index`。
- MinerU 正常输出能被适配。
- MinerU 不可执行时，文本型 PDF 明确进入 pdfplumber。
- 损坏 PDF 返回 `PDF_INVALID`。
- 无文本扫描件在未启用 OCR 时返回 `PARSE_QUALITY_LOW`，不能伪装成功。
- 临时目录在成功和失败后都被清理。

通过门槛：文本型夹具页码映射全部正确；失败均有错误码。

### 阶段 2：Hy3 联合生成

**允许修改**：`prompts.py`、`hy3_service.py`、`settings.py`、`test_hy3_service.py`。

任务：

1. 先实现 Mock 模式并通过所有契约测试。
2. 实现 OpenAI 兼容客户端，Key 从环境变量读取。
3. 执行真实接口探针，不接前端。
4. 使用严格 JSON Schema；Pydantic 每层设置 `extra="forbid"`。
5. Schema 失败最多重试 2 次，重试只附加字段错误摘要。
6. 记录模型 ID、Prompt 版本、参数、token、延迟、重试和错误码，不记录完整论文。

测试：

```powershell
python -m pytest backend/tests/test_hy3_service.py -q
python -m pytest backend/tests/test_models.py backend/tests/test_hy3_service.py -q
```

必须覆盖：

- Mock 合法响应通过。
- 非 JSON、缺字段和额外字段触发受限重试。
- 第 3 次仍失败返回 `SCHEMA_INVALID`。
- 不存在的 `candidate_block_id` 在后续核验前仍可解析，但不得成为已验证证据。
- 日志中没有 API Key 和大段原文。
- Live 模式缺 Key 返回 `HY3_CONFIG_MISSING`，不回退 Mock。

通过门槛：Mock 全绿，并至少保存一次真实 Hy3 合法结构化响应的运行记录。

### 阶段 3：证据核验、快速检查和评分

**允许修改**：常规任务为 `audit_service.py`、`test_audit_service.py`，必要时补充 `models.py`。深审 v2 属于一次原子协议迁移；只有用户明确授权时，才允许在同一任务同步修改 `models.py`、`prompts.py`、`hy3_service.py`、`audit_service.py`、`deep_audit_valid.json`、`test_models.py`、`test_hy3_service.py` 和 `test_audit_service.py`。该例外只用于将 v1 原子替换为 v2，不得新增并行协议、API、数据库或前端改动。

任务：

1. 核验候选 block 是否存在。
2. 对引文执行 Unicode、空白、换行和断词规范化后匹配。
3. 候选无效时使用 BM25 + 数字/单位/否定词精确匹配召回 Top-3。
4. 实现页码、引文、数字、单位、否定词、比较方向和必需区检查。
5. 实现条件拆分触发器，但不对所有句子递归调用。
6. 批量调用 `Hy3Service.deep_audit` 获取唯一的 `DeepAuditResult` v2；输入包含完整 `ContentDraft` 和已验证语义配对，代码合并 `ComplianceContext`、生成 `RiskAssessment` 后计算八维分数和双门槛。
7. non-auditable 内容不进入事实支持率分母，但必须进入完整文档的风险检查。
8. 风险合规不得复用通用 `SemanticJudgment.severity`，不得解析 `reason` 猜类别；只有结构化 `ComplianceContext` 与 `RiskFinding` 可进入风险等级映射。

测试：

```powershell
python -m pytest backend/tests/test_models.py backend/tests/test_hy3_service.py backend/tests/test_audit_service.py -q
```

必须覆盖：

- 正确 block 和引文验证成功。
- 假 block、假引用、数字改变、单位改变、否定反转被检出。
- 引文只有换行或连字符差异时可以规范化匹配。
- 没有证据时返回 `insufficient`。
- `quick_complete` 的总分和合格状态为 `null/pending_deep_audit`。
- 只有八维语义结果齐全时才生成完整总分。
- 硬失败不能被其他维度高分抵消。
- 权重总和严格等于 1。
- 纯事实、范围或术语问题不会重复扣风险合规分。
- 三类风险检查缺失、重复、额外，`RiskLocation` 类型/句子/摘录无效，或 `RiskAssessment` 缺失、不完整、等级不一致时返回 `AUDIT_INCOMPLETE`，不生成完整结论。
- `detected/not_detected/unclear` 的位置数量约束、多位置保存、敏感信息摘录为空及脱敏理由/修复建议均有测试。
- 一项提示缺失、两项提示缺失/中风险、高风险和严重风险分别稳定映射为 3、2、1、0 级；三类 `detected` 触发对应代码硬失败。
- 标识适用但缺失稳定映射为 0 级但不生成硬失败；没有其他硬失败时结论为 `needs_revision`。
- `quick_complete` 的 `risk_assessment=null`；`deep_complete` 保存完整 `RiskAssessment`，且其 `level_points` 与风险合规维度一致并能随 `AuditReport` JSON 往返保留。

通过门槛：黄金主张与证据样例全部符合预期；深审 v2 的语义配对、三类风险检查、完整位置模型和摘录全部可核验；`RiskAssessment` 可完整持久化且等级与维度一致；风险评分公式、权重、严格硬失败映射和双门槛测试全绿；Hy3 未直接控制风险等级、分数或最终结论。

### 阶段 4：存储和 API 闭环

**允许修改**：`project_store.py`、`api.py`、`main.py`、`test_project_store.py`、`test_api.py`。

任务：

1. 用 `sqlite3` 建立 `projects/versions/audits/patches/runs` 五张表。
2. 数据库只保存项目状态和 JSON 快照，不进行复杂关系建模。
3. 实现第 4.2 节固定 API。
4. 上传类型、大小和权限确认在进入解析前检查。
5. 每个阶段成功后提交事务；失败保存 `run` 记录但不破坏上一稳定状态。

测试：

```powershell
python -m pytest backend/tests/test_project_store.py backend/tests/test_api.py -q
python -m pytest backend/tests -q
```

必须覆盖：

- 未确认权限的上传被拒绝。
- 非 PDF 和超限文件被拒绝。
- 完成上传、生成、快速检查和完整审计的 API 流程。
- 重复读取返回相同当前版本。
- API 失败后旧版本仍可读取。
- 不存在的项目、版本和补丁返回稳定错误码。

通过门槛：后端全部测试通过；使用 Mock 能通过命令行完成核心闭环。

### 阶段 5：三栏工作台和证据跳转

**允许修改**：`frontend/src/` 和前端测试，不修改后端契约。

任务：

1. `App.tsx` 只管理项目级状态和三栏布局。
2. `PdfPane.tsx` 负责 PDF 加载、页码、缩放、跳页和文本查找。
3. `DocumentPane.tsx` 按 `sentence_id` 渲染当前文档并处理选择。
4. `SidePanel.tsx` 根据状态显示证据、快速检查、深度审计、修改和版本历史。
5. `api.ts` 集中封装全部请求和错误映射。
6. 页面必须区分等待、失败、证据不足、快速检查完成和完整审计完成。

测试：

```powershell
cd frontend
npm run test -- --run
npm run build
npx playwright test
```

必须覆盖：

- 未上传、解析中、生成中、快速检查和完整审计状态。
- 点击句子后正确设置目标页并展示证据摘录。
- 文本查找失败时仍停留正确页并明确显示摘录，不出现空白界面。
- 深度审计未完成时不显示总分和合格状态。
- API 错误可见且允许重试。
- 桌面 1440x900 和移动 390x844 下文字不重叠；移动端允许切换三个面板。

通过门槛：前三个预设流程任务通过 Playwright；前端构建通过。

### 阶段 6：修订、版本和回退

**允许修改**：`hy3_service.py`、`project_store.py`、`api.py`、`SidePanel.tsx`、相关测试。

任务：

1. 句子修改只提交目标 `sentence_id`、当前文本、证据和用户意图。
2. 全文修改提交当前五区内容，不提交历史版本。
3. 修改结果只生成补丁预览，不直接应用。
4. 接受补丁前检查版本号、`before_hash` 和范围。
5. 接受后创建不可变新版本并自动运行快速检查。
6. 回退通过复制历史快照创建新版本，不移动或删除历史记录。

测试：

```powershell
python -m pytest backend/tests/test_hy3_service.py backend/tests/test_project_store.py backend/tests/test_api.py -q
cd frontend
npm run test -- --run
npx playwright test
```

必须覆盖：

- 合法句子补丁可预览、拒绝和接受。
- 越界补丁、过期 hash 和不存在句子被拒绝。
- 拒绝补丁不改变当前版本。
- 接受补丁生成新版本并保留旧版本。
- 回退后内容、主张和审计快照一致。
- 句子修改不改变未选择句子。
- 全文修改后必须重新生成主张，旧深度审计标记为过期。

通过门槛：五个预设流程任务全部通过；不存在无确认自动覆盖。

### 阶段 7：评测脚本和结果报告

**允许修改**：`eval/`、`reports/`、评测调用所需的只读服务接口。

开发期先用冒烟集验证脚本：

- 1 篇论文的好/中/差 3 份输出。
- 数字篡改、假引用 2 类攻击及配对干净版本。
- 2 份固定输出各运行 2 次。

最终运行不得用冒烟规模代替：

- 开发集 5 篇各 3 档，共 15 份，用于校准。
- 保留集另选 5 篇各 3 档，共 15 份，用于最终判别。
- 8 类攻击各 2 个，共 16 个，并包含 16 个配对干净版本。
- 12 份固定输出各运行 3 次，共 36 次稳定性评估。
- 修订前后错误解决率、新增严重错误和无关内容变更率。

`run_eval.py` 只保留一个入口：

```powershell
python eval/run_eval.py --mode smoke
python eval/run_eval.py --mode calibrate
python eval/run_eval.py --mode final
python eval/run_eval.py --mode stability
python eval/build_report.py
```

测试：

```powershell
python -m pytest eval/test_eval.py -q
python eval/run_eval.py --mode smoke
python eval/build_report.py --input reports/smoke_results.jsonl
```

通过门槛：

- 中断后可按 `case_id + run_index` 续跑，不重复收费。
- 每条结果包含模型、Prompt、Schema、数据和代码版本。
- 失败、超时和不支持样例进入结果表。
- 报告完全由原始 JSONL 生成，不手工修改数字。
- 最终保留集开始前冻结配置；运行后不得按结果移动门槛。

### 阶段 8：发布检查

任务：

1. 从全新环境按 README 安装并启动。
2. 运行后端全测、前端构建、Playwright 和评测冒烟。
3. 扫描密钥、私密 PDF、绝对本机路径和许可不明全文。
4. 核对 `.env.example`、许可证、第三方归属和 AI 说明。
5. 录制不超过两分钟的完整闭环演示。
6. 保存最终测试命令、日期、结果和已知限制。

最终命令：

```powershell
python -m pytest backend/tests eval/test_eval.py -q
cd frontend
npm run test -- --run
npm run build
npx playwright test
```

通过门槛：所有 P0 通过；P1 未完成项有明确说明；仓库没有密钥和私密全文；演示使用真实 Hy3 或明确标注的预置结果，不能把 Mock 冒充真实调用。

## 9. AI 开发约束

### 9.1 每次任务开始前

AI 必须先输出以下五项，再允许编辑代码：

```text
本次目标：一句话说明唯一目标
允许修改：列出最多 4 个文件
禁止修改：列出本次不相关模块
输入输出：引用 DEV_PLAN 中的数据契约或 API
验证命令：列出本次完成后必须运行的命令
```

一次任务最多完成一个可独立验收行为。例如“实现 SourceBlock 模型并测试”是一个任务；“完成全部后端和前端”不是任务。

### 9.2 编码硬规则

1. 先读目标文件和直接相关测试，不扫描或重写整个仓库。
2. 不得创建计划外目录、接口、数据库表、Schema 字段和依赖。
3. 不得把字典当长期数据契约；跨模块数据必须使用 `models.py` 中的类型。
4. 不得在路由中写业务算法，不得在 React 组件中直接拼 API URL。
5. 不得让 Hy3 生成页码、bbox、总分和双门槛结论。
6. 不得在真实 API 失败后返回 Mock 成功。
7. 不得吞掉异常或只返回 HTTP 500；必须映射稳定错误码。
8. 不得为了通过测试删除断言、扩大容差或硬编码夹具答案到生产分支。
9. 不得顺手重构无关文件；单次差异过大时拆成下一任务。
10. 每个任务先运行针对性测试，再运行所在阶段的回归测试。

### 9.3 AI 任务指令模板

```text
请只完成 DEV_PLAN 阶段 X 的任务 Y。

目标：
<一个可观察行为>

允许修改：
- <file 1>
- <file 2>
- <test file>

固定契约：
- 输入：<model/API>
- 输出：<model/API>
- 错误：<error codes>

禁止：
- 新增依赖或目录
- 修改无关 API/Schema
- 用 Mock 掩盖 Live 失败

先补测试，再写最少实现。完成后运行：
<exact command>

最后只报告：修改文件、关键行为、测试结果、尚未解决的问题。
```

### 9.4 允许停止并请求决策的情况

- 官方 API 与既定 Schema 能力不兼容。
- MinerU 当前版本无法提供方案依赖的页码信息。
- 新依赖不可避免。
- 一个改动必须修改超过 4 个计划外文件。
- 测试暴露方案契约本身矛盾。

遇到这些情况时不得自行创造另一套架构。

## 10. 测试总表

| 部分 | 最低测试 | 完成判定 |
|---|---|---|
| 数据契约 | 合法、缺字段、额外字段、非法枚举、重复 ID | 非法输入全部被拒绝 |
| PDF 解析 | 正常、损坏、无文本、MinerU 失败、降级和清理 | 页码正确，失败可诊断 |
| Hy3 调用 | Mock、Live 探针、Schema 重试、缺 Key、日志脱敏 | 同一模型校验链生效 |
| 证据核验 | block、引文、数字、单位、否定、BM25 降级 | 假引用和数字篡改被检出 |
| 八维审计 | 快速/完整状态、权重、硬失败、双门槛 | 模型不直接控制总分 |
| API | 正常流程、非法状态、失败恢复、稳定错误码 | Mock 端到端通过 |
| PDF 界面 | 页码跳转、摘录、文本查找失败降级 | 不超过两次操作到证据 |
| 修订 | 预览、拒绝、接受、越界、过期、回退 | 无确认不改文档 |
| 前端流程 | 五个预设任务、等待和错误状态 | Playwright 全绿 |
| 评测 | 冒烟、续跑、冻结、结果生成 | 报告可由 JSONL 重建 |
| 安全 | Key、私密全文、路径和日志检查 | 违规项为 0 |

## 11. 每日执行顺序

每天按固定顺序工作：

1. 从当前阶段选择一个未完成任务卡。
2. 写出目标、允许文件、契约和测试命令。
3. 先创建或补充失败测试。
4. 写满足测试的最小实现。
5. 运行针对性测试。
6. 运行本阶段回归测试。
7. 检查是否新增了计划外依赖、目录、字段或接口。
8. 记录真实结果和阻塞，不把“设计完成”写成“代码已验证”。

任何测试未通过时，不进入下一阶段。临近截止日期时，按照 P2、P1、P0 的顺序削减能力；不得先削减页码证据、完整审计、句子修改、版本回退和最终评测。

## 12. 完成定义

一个功能只有同时满足以下条件才算完成：

- 代码已实现，不是占位函数或固定假数据。
- 输入、输出和错误符合本文件契约。
- 正常、边界和失败测试均已运行并通过。
- Mock 与 Live 的差异被明确标记。
- 界面存在等待、失败和证据不足状态。
- 没有新增未经批准的依赖、目录和功能。
- `requirements.lock` 与应用环境的非 editable `pip freeze` 一致，且不含本机路径或密钥。
- README 中有真实可执行的安装、运行和验证命令。

PaperLens 的工程目标不是堆叠模块，而是用最少的稳定组件完成“解析、生成、核验、审计、修改、复核、回退和评测”闭环。任何不能提高这条闭环可靠性或可验证性的功能，都不进入 V1.0。
