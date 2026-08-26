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
| `POST /api/projects` | 上传并解析 PDF | 项目、解析质量、`SourceBlock` 统计 | `PDF_INVALID`、`PARSE_FAILED`、`PARSE_QUALITY_LOW`、`RIGHTS_NOT_CONFIRMED` |
| `GET /api/projects/{id}` | 读取完整当前状态 | 当前文档、审计、版本列表 | `PROJECT_NOT_FOUND` |
| `GET /api/projects/{id}/pdf` | 读取本地 PDF | PDF 文件流 | `PDF_NOT_FOUND` |
| `POST /api/projects/{id}/generate` | 按显式 claims 策略联合生成并自动快速检查 | 文档、主张、规则结果 | `PROJECT_NOT_READY`、`HY3_CONFIG_MISSING`、`HY3_UNAVAILABLE`、`SCHEMA_INVALID`、`AUDIT_INCOMPLETE` |
| `POST /api/projects/{id}/audit` | 批量运行完整语义审计和评分 | 完整 `AuditReport` | `PROJECT_NOT_READY`、`EVIDENCE_NOT_READY`、`HY3_CONFIG_MISSING`、`SCHEMA_INVALID`、`AUDIT_INCOMPLETE`、`HY3_UNAVAILABLE` |
| `POST /api/projects/{id}/revisions` | 生成补丁预览，不改当前版本 | `EditPatch` | `TARGET_STALE`、`PATCH_INVALID` |
| `POST /api/projects/{id}/revisions/{patch_id}/accept` | 接受补丁并生成新版本 | 新版本和快速检查结果 | `TARGET_STALE`、`PATCH_INVALID` |
| `POST /api/projects/{id}/versions/{version_id}/restore` | 复制历史版本为新当前版本 | 新版本 | `VERSION_NOT_FOUND` |
| `GET /api/projects/{id}/export` | 导出当前 Markdown 与来源说明 | `.md` 文件 | `PROJECT_NOT_READY` |

不允许在开发过程中创建功能重复的路径。若确需改变 API，必须同时修改本节、`types.ts`、后端契约测试和前端 API 测试。

API 路径在本节一次冻结，但实现按阶段交付，禁止为了让路径提前存在而创建占位路由：

- 阶段 4 只实现核心闭环所需的 `POST /api/projects`、`GET /api/projects/{id}`、`GET /api/projects/{id}/pdf`、`POST /api/projects/{id}/generate` 和 `POST /api/projects/{id}/audit`。
- 阶段 6 实现 `POST /api/projects/{id}/revisions`、补丁接受、版本恢复和 Markdown 导出；这些路径在阶段 4 不得以固定错误、空对象或未实现响应占位。
- 阶段 5 前端只能调用阶段 4 已真实实现的核心路径；修订、恢复和导出 UI 随阶段 6 一并启用。

### 4.3 SQLite 最小表结构

数据库固定为五张表，不建立通用实体系统：

| 表 | 必需字段 |
|---|---|
| `projects` | `id`、`stage`、`pdf_path`、`pdf_sha256`、`rights_confirmed`、`parse_json`、`current_version_id`、`error_code`、`created_at`、`updated_at` |
| `versions` | `id`、`project_id`、`version_no`、`parent_version_id`、`content_json`、`claims_json`、`reason`、`created_at` |
| `audits` | `id`、`project_id`、`version_id`、`status`、`evidence_json`、`report_json`、`created_at` |
| `patches` | `id`、`project_id`、`base_version_id`、`status`、`patch_json`、`created_at` |
| `runs` | `id`、`project_id`、`version_id`、`operation`、`mode`、`status`、`metadata_json`、`usage_json`、`error_code`、`started_at`、`ended_at` |

所有 JSON 列在写入前必须经过 Pydantic 校验。`status/stage` 使用固定枚举；不允许在 SQL 中保存 Python pickle。删除项目时再清理其 PDF 和解析临时文件，普通重试不得删除上一个稳定版本。

新增字段的固定语义如下：

- `projects.rights_confirmed` 使用 SQLite `INTEGER NOT NULL CHECK (rights_confirmed IN (0, 1))`。只有上传请求显式确认权限后才能创建项目并写入 `1`；后续生成和深审从该列重建 `ComplianceContext.rights_or_license_confirmed`，不得相信客户端重复声明。
- `audits.evidence_json` 保存经 Pydantic 校验的 `EvidenceRecord[]` 快照。快速检查与同一版本的完整审计必须使用同一组已验证证据；不得从 Hy3 响应恢复页码、bbox 或引文，也不得在读取 API 时静默重新计算并覆盖已保存证据。
- `projects.parse_json` 保存严格的解析快照，包含 `SourceBlock[]` 与解析质量；`versions.content_json` 保存 `ContentDraft`，`versions.claims_json` 保存 `AtomicClaim[]`，`audits.report_json` 仍只保存同一份完整 `AuditReport`。
- 可读错误消息、是否可重试和可重试阶段保存在最新失败 `runs.metadata_json`；不再向 `projects` 增加重复错误字段。项目即使处于 `failed`，最后一个稳定版本和审计快照仍可读取。
- SQLite 文件固定为 `PAPERLENS_DATA_DIR/paperlens.db`；PDF 固定保存为 `PAPERLENS_DATA_DIR/projects/<project_id>/source.pdf`。客户端文件名不得参与存储路径，路径必须经解析后仍位于项目目录内。

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
- `category=sensitive_information` 时，每个 `RiskLocation.evidence_excerpt` 必须为 `null`。Hy3 返回的 `reason` 和 `remediation` 只作为未信任的临时输入：`AuditService` 完成结构、状态和位置核验后，必须按 `detected/not_detected/unclear` 无条件替换为代码固定的脱敏说明，再构造任何公开返回或持久化对象。不得通过邮箱、电话、姓名、地址、ISBN、DOI、试验编号或其他格式启发式决定是否替换。

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
- `AuditService.run_deep_audit` 必须先验证 `ComplianceContext`。`rights_or_license_confirmed=false` 时，在发送完整文档前返回不可重试的 `RIGHTS_NOT_CONFIRMED`，Hy3 调用次数为 0；离线 `score` 仍可按冻结映射计算 1 级，用于确定性测试。
- `AuditService.run_deep_audit` 返回的 `DeepAuditResult` 必须与 `AuditReport.risk_assessment.risk_findings` 使用同一组已校验、已脱敏风险结果；不得把 Hy3 原始 `sensitive_information.reason/remediation` 暴露给调用方、日志或数据库。

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
      "reason": "No sensitive-content risk was detected.",
      "remediation": "No sensitive-content remediation is required."
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

三个字段全部必填；`risk_findings` 必须保存校验通过的三个类别及其全部位置、理由和修复建议，其中 `sensitive_information` 只能保存代码固定脱敏文本。`level_points` 必须是代码按下表计算的 0 至 4 整数。`RiskAssessment` 在构造和反序列化时都必须复算风险等级、三个类别完整性和敏感类别固定文本；任何缺失或不一致均为 `AUDIT_INCOMPLETE`。

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
        "reason": "No sensitive-content risk was detected.",
        "remediation": "No sensitive-content remediation is required."
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

`AuditReport.risk_assessment` 的类型固定为 `RiskAssessment|null`。`quick_complete` 必须使用 `dimensions=[]`、`risk_assessment=null`、`hard_failures=[]`、`core_gate_passed=null`、`overall_score=null` 和 `pending_deep_audit`；证据问题只保存在 `EvidenceRecord.rule_flags`。`deep_complete` 必须包含非空 `RiskAssessment`，且其 `level_points` 必须与 `risk_compliance` 维度 `raw_metrics.level_points` 一致。阶段 4 的 API 返回完整 `AuditReport`，`audits.report_json` 保存同一完整对象，因此风险位置、代码脱敏后的敏感类别说明、其他类别理由和修复建议必须随报告持久化，不能在 API 或存储边界丢失。

`AuditReport` 在构造和 JSON 反序列化时必须重新核对八个维度各自的 `level_points/score/level`、风险等级与风险维度、三类 `detected` 与固定风险硬失败、加权总分、核心门槛和最终 `decision`。任何硬失败固定产生 `unqualified`；必要标识缺失本身不产生硬失败，但风险等级为 0、核心门槛失败且在无其他硬失败时只能是 `needs_revision`。外部输入不得通过同时伪造多个相互一致的展示字段绕过代码计算。

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

### 5.8 阶段 4 存储快照与核心 API 契约

阶段 4 的第一个原子任务必须在 `models.py` 中补齐严格 Pydantic 契约，并在 `test_models.py` 中先写失败测试。不得在 `api.py` 或 `project_store.py` 临时定义请求、响应或 JSON 列字典协议。该任务只允许新增本节模型，不得修改阶段 1 至 3 已冻结模型的字段、枚举或验证规则。

JSON 列使用以下包装模型：

- `ParseQualitySnapshot {page_count, block_count, empty_page_rate, abnormal_character_rate, page_number_completeness_rate, bbox_availability_rate}`；计数非负，比例限定在 0 至 1。
- `ParseSnapshot {blocks: SourceBlock[], quality: ParseQualitySnapshot}`，写入 `projects.parse_json`。
- `ClaimsSnapshot {claims: AtomicClaim[]}`，写入 `versions.claims_json`；`versions.content_json` 单独保存 `ContentDraft`。
- `EvidenceSnapshot {evidence_records: EvidenceRecord[]}`，写入 `audits.evidence_json`。
- `UsageSnapshot {prompt_tokens, completion_tokens, total_tokens}`，三个字段均为非负整数或 `null`，写入 `runs.usage_json`。
- `RunMetadata {message, retryable, retryable_stage, model, prompt_version, schema_version}`；除 `retryable` 外均允许 `null`，只保存脱敏错误说明和短元数据，写入 `runs.metadata_json`。
- `runs.operation` 只允许 `parse/generate/quick_check/deep_audit`，`runs.mode` 只允许 `local/mock/live`，`runs.status` 只允许 `succeeded/failed`。这些枚举同样定义在 `models.py`。

阶段 4 核心 API 使用以下请求与响应模型；时间字段使用带时区 UTC `datetime`，所有对象 `extra="forbid"`：

```text
ProjectCreateResponse
  project_id: Identifier
  stage: parsed
  parse_quality: ParseQualitySnapshot
  source_block_count: int >= 0
  created_at: datetime

VersionSummary
  version_id: Identifier
  version_no: int >= 1
  parent_version_id: Identifier|null
  reason: string
  created_at: datetime

ProjectView
  project_id: Identifier
  stage: ProjectStage
  model_mode: mock|live
  parse_quality: ParseQualitySnapshot|null
  source_block_count: int >= 0
  current_version_id: Identifier|null
  current_version_no: int|null
  document: ContentDraft|null
  claims: AtomicClaim[]
  evidence_records: EvidenceRecord[]
  audit_report: AuditReport|null
  versions: VersionSummary[]
  error_code: string|null
  retryable_stage: ProjectStage|null
  created_at: datetime
  updated_at: datetime

GenerationRequest
  claim_policy: required|must_be_empty

GenerationResponse
  project_id: Identifier
  version_id: Identifier
  stage: quick_checked
  model_mode: mock|live
  document: ContentDraft
  claims: AtomicClaim[]
  evidence_records: EvidenceRecord[]
  quick_report: AuditReport

DeepAuditRequest
  source_disclosure_status: present|missing
  ai_assistance_disclosure_status: present|missing
  generated_content_label_applicability: applicable|not_applicable
  generated_content_label_status: present|missing|not_applicable

DeepAuditResponse
  project_id: Identifier
  version_id: Identifier
  stage: deep_audited
  audit_report: AuditReport
```

请求和状态约束固定如下：

- `POST /api/projects` 使用 multipart，字段固定为 `file` 和 `rights_confirmed`。`rights_confirmed` 不是 `true` 时必须在读取或保存 PDF 前返回 `RIGHTS_NOT_CONFIRMED`；不得创建项目、PDF 或 run 记录。文件名必须以 `.pdf` 结尾且内容以 PDF 魔数开头；读取时按 `MAX_PDF_MB + 1 byte` 有界检查，超限后立即停止并删除本请求的临时文件。
- `POST /api/projects/{id}/generate` 必须接收严格 JSON `GenerationRequest`，调用方必须显式提交且只能提交 `claim_policy`，不得提交 `SourceBlock`、证据、页码、bbox、评分或 rights；字段缺失、非法枚举或额外字段统一返回 HTTP 502 / `AUDIT_INCOMPLETE`，不得暴露框架原始验证细节。`claim_policy=required` 要求 `GeneratedBundle.claims` 至少 1 条；`claim_policy=must_be_empty` 要求其恰好为 0 条。不匹配时不得补齐、过滤或改写供应商输出，统一返回 `AUDIT_INCOMPLETE`。服务只读取已保存并重新通过 `ParseSnapshot` 校验的 `SourceBlock[]`；首次供应商结果和快速检查失败后复用的已保存 bundle 必须经过 `Hy3Service` 同一 claims 数量策略校验，复用时不得再次调用供应商。policy 不匹配时不得运行快速检查、创建新版本、审计或伪造 run，并保留已有稳定 bundle 与失败状态；同 policy 恢复继续只重试快速检查。`Hy3Service.generate` 成功后先用一个事务保存初始版本并将稳定阶段推进到 `generated`；随后运行 `AuditService.quick_check`，再用第二个事务保存证据快照、快速报告并推进到 `quick_checked`。快速检查失败不得删除已成功生成的版本。
- `POST /api/projects/{id}/audit` 只接收 `DeepAuditRequest`。API 从 `projects.rights_confirmed` 构造 `ComplianceContext.rights_or_license_confirmed`，不得接受客户端覆盖；其余四个披露和标识字段来自请求。完整审计只读取当前版本已保存的 `ContentDraft`、`AtomicClaim[]` 和 `EvidenceRecord[]`。
- `GET /api/projects/{id}` 返回 `ProjectView`，只从已通过 Pydantic 重新验证的数据库快照构造；不得暴露 `pdf_path`、SQL 行、Hy3 原始响应或供应商敏感自由文本。
- `GET /api/projects/{id}/pdf` 只返回当前项目固定 `source.pdf`，并设置 `application/pdf`；数据库路径缺失、越界或文件不存在统一返回 `PDF_NOT_FOUND`。
- Mock/Live 模式只来自后端 `Settings`，客户端不能切换。`model_mode` 必须进入生成响应和项目视图，使前端能够显示 `MOCK`。

现有错误码的 HTTP 映射固定如下，不新增同义错误码：

| HTTP | 错误码 |
|---:|---|
| 400 | `PDF_INVALID` |
| 403 | `RIGHTS_NOT_CONFIRMED` |
| 404 | `PROJECT_NOT_FOUND`、`PDF_NOT_FOUND`、`VERSION_NOT_FOUND` |
| 409 | `PROJECT_NOT_READY`、`EVIDENCE_NOT_READY`、`TARGET_STALE` |
| 413 | `PDF_INVALID`，并在安全 `details.reason` 中标记 `file_too_large` |
| 422 | `PARSE_QUALITY_LOW`、`PATCH_INVALID` |
| 502 | `SCHEMA_INVALID`、`AUDIT_INCOMPLETE` |
| 503 | `PARSE_FAILED`、`HY3_CONFIG_MISSING`、`HY3_UNAVAILABLE` |

上传字段缺失或类型错误仍使用对应的 `PDF_INVALID`/`RIGHTS_NOT_CONFIRMED`；`GenerationRequest` 缺失、非法枚举或额外字段，以及 `DeepAuditRequest` 结构或组合无效，均使用 HTTP 502 / `AUDIT_INCOMPLETE`。两条路由分别返回安全的 generation request / deep-audit request 错误消息。所有业务失败都返回现有 `ErrorResponse`，不得把 FastAPI 默认验证体、Python 异常、SQL、绝对路径、Prompt、原始论文或 API Key 返回给客户端。

## 6. Hy3 Prompt 模板

所有 Prompt 保存在 `backend/app/prompts.py`，使用版本常量，例如 `GENERATION_PROMPT_VERSION = "gen-v3"`。不得把 Prompt 分散在路由、测试和前端。Generation 跨重试安全契约错误改为按 attempt 顺序累计并要求同时修复后升级 Prompt 版本；`GeneratedBundle` 输出结构未改变，因此生成 Schema 版本仍为 `generated-bundle-v1`。

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
- claim_policy: {{claim_policy}}
- paper_metadata: {{paper_metadata_json}}
- source_blocks: {{source_blocks_json}}

claim_policy=required 时，claims 必须至少包含 1 项。
claim_policy=must_be_empty 时，claims 必须严格为空数组；不得删除或过滤非空供应商输出来满足策略。
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
5. Generation 的 JSON、Pydantic 或 claim policy 失败最多重试 2 次；每次重试按 attempt 顺序附加此前全部安全契约错误摘要，并要求同时修复且不得重新违反先前约束。每项摘要最多 800 字符，不含 Pydantic input、供应商原始响应、Prompt、论文或 claims 文本。
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

**验收状态**：阶段 3 已在提交 `be5adf3` 通过独立验收；验收基线为阶段 3 聚焦 `181 passed`、后端 `208 passed, 1 skipped`，跳过项仅为需显式开启的真实 MinerU 集成。后续阶段不得在无新失败测试和单独授权时修改阶段 3 的检索、深审、风险或评分行为。

**允许修改**：常规任务为 `audit_service.py`、`test_audit_service.py`，必要时补充 `models.py`。深审 v2 属于一次原子协议迁移；只有用户明确授权时，才允许在同一任务同步修改 `models.py`、`prompts.py`、`hy3_service.py`、`audit_service.py`、`deep_audit_valid.json`、`test_models.py`、`test_hy3_service.py` 和 `test_audit_service.py`。该例外只用于将 v1 原子替换为 v2，不得新增并行协议、API、数据库或前端改动。

任务：

1. 核验候选 block 是否存在。
2. 对引文执行 Unicode、空白、换行和断词规范化后匹配。
3. 候选无效时使用 BM25 + 数字/单位/否定词精确匹配召回 Top-3。证据切片先完成 Unicode、空白和断词换行规范化，再生成原子片段与相邻两片段窗口；不得在小数点或 `e.g.`、`Fig. 2`、`Dr. Smith` 等缩写内部截断。同一 block 只保留一个候选，优先覆盖更多主张约束、覆盖相同时选择更短片段，不同 block 再按 BM25 排序。
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
- 邮箱、电话、姓名、地址、ISBN、DOI、试验编号和未知格式的供应商敏感自由文本均不得进入 `DeepAuditResult` 或 `AuditReport`；实现依靠无条件代码替换，不依靠格式识别。普通学术术语、数字范围和混合字母数字文本不得因此触发 `AUDIT_INCOMPLETE`。
- 权限未确认必须在 Hy3 调用前返回 `RIGHTS_NOT_CONFIRMED`，并断言调用次数为 0。
- 一项提示缺失、两项提示缺失/中风险、高风险和严重风险分别稳定映射为 3、2、1、0 级；三类 `detected` 触发对应代码硬失败。
- 标识适用但缺失稳定映射为 0 级但不生成硬失败；没有其他硬失败时结论为 `needs_revision`。
- `quick_complete` 的 `risk_assessment=null`；`deep_complete` 保存完整 `RiskAssessment`，且其 `level_points` 与风险合规维度一致并能随 `AuditReport` JSON 往返保留。

通过门槛：黄金主张与证据样例全部符合预期；深审 v2 的语义配对、三类风险检查、完整位置模型和摘录全部可核验；`RiskAssessment` 可完整持久化且等级与维度一致；风险评分公式、权重、严格硬失败映射和双门槛测试全绿；Hy3 未直接控制风险等级、分数或最终结论。

### 阶段 4：存储和 API 闭环

**允许修改**：阶段整体允许 `models.py`、`project_store.py`、`api.py`、`main.py`、`test_models.py`、`test_project_store.py`、`test_api.py`，但每个原子任务仍最多修改 4 个文件。`models.py` 和 `test_models.py` 只允许用于第 5.8 节新增的存储/API 契约，不得改动阶段 1 至 3 已冻结模型、枚举、评分常量或验证器。

任务：

1. 先在 `models.py` 与 `test_models.py` 完成第 5.8 节存储快照、运行元数据和核心 API 请求/响应契约。
2. 用 `sqlite3` 建立 `projects/versions/audits/patches/runs` 五张表，包含已授权的 `projects.rights_confirmed` 和 `audits.evidence_json`，不增加第六张表或 ORM。
3. 数据库只保存项目状态和经 Pydantic 校验的 JSON 快照，不进行复杂关系建模。
4. 只实现第 4.2 节明确分配给阶段 4 的五个核心 API；修订、接受、恢复和导出留到阶段 6，不创建占位路由。
5. 上传类型、大小和权限确认在进入解析及写入正式 PDF 前检查；后续深审权限只读取项目记录。
6. 每个阶段成功后提交事务；失败保存脱敏 run 记录但不破坏上一稳定版本、审计或证据快照。

原子任务顺序固定为：

1. `models.py + test_models.py`：只完成第 5.8 节契约红测与最小模型。
2. `project_store.py + test_project_store.py`：建表、事务、Pydantic JSON 往返和失败恢复。
3. `api.py + main.py + test_api.py`：上传、项目读取和 PDF 读取，支持可注入的测试依赖。
4. `api.py + test_api.py`：生成、自动快速检查和原子保存。
5. `api.py + test_api.py`：完整审计、权限重建、完整 `AuditReport` 保存和稳定错误映射。
6. `test_api.py` 为主：Mock 命令行核心闭环与失败恢复；只有真实缺陷才回到对应获准生产文件。

测试：

```powershell
python -m pytest backend/tests/test_models.py backend/tests/test_project_store.py backend/tests/test_api.py -q
python -m pytest backend/tests -q
```

必须覆盖：

- 未确认权限的上传被拒绝。
- 非 PDF 和超限文件被拒绝。
- 完成上传、生成、快速检查和完整审计的 API 流程。
- 重复读取返回相同当前版本。
- API 失败后旧版本仍可读取。
- 不存在的项目和 PDF 返回稳定错误码；版本、补丁和修订路径留到阶段 6 测试。
- 权限未确认不创建项目、PDF 或 run；生成和审计不接受客户端 SourceBlock、已验证证据、页码、bbox、分数或 rights 覆盖。
- 生成必须显式接收严格 `GenerationRequest.claim_policy`；缺失、非法或额外字段使用 HTTP 502 / `AUDIT_INCOMPLETE`。首次结果与恢复复用的 bundle 都经过同一策略校验，两个 policy 方向不匹配时均不增加供应商、快速检查、版本、审计或 run 副作用。
- `parse_json/content_json/claims_json/evidence_json/report_json/metadata_json/usage_json` 写入前和读取后都经过固定 Pydantic 模型验证。
- 完整审计保存并返回阶段 3 已脱敏的风险结果；数据库和 API 中不存在 Hy3 原始敏感自由文本。

通过门槛：后端全部测试通过；使用 Mock 能通过命令行完成核心闭环。

**阶段 4 收口状态（2026-08-25）**：`STAGE_4_FORMAL_GATE=PASS`。当前收口基线为后端 `267 passed, 1 skipped`，跳过项仅为需显式启用的真实 MinerU 集成测试；Mock 五路由命令行闭环、五表存储、Pydantic JSON 往返、失败恢复和稳定错误映射均已通过。阶段 4 的正式门槛不要求真实 Live 公共 API 全链路成功，因此允许在保持下述已知阻塞可见的前提下进入阶段 5。该结论不等于 `PRODUCTION_READY`，也不得把 Mock 结果描述为真实 Hy3 结果。

**LIVE-BLOCKER-01：真实 risk-only 深审未闭环。** 已使用 `simple_2page.pdf` 完成一次受控真实公共 API 复验：真实 MinerU 上传解析返回 `201/parsed`（2 页、4 个 `SourceBlock`）；真实 Hy3 生成返回 `200/quick_checked/live`，但产生 `0 claims/0 evidence`；随后 risk-only 深审在三次真实 Hy3 响应（`retries=2`）后仍返回 `502/AUDIT_INCOMPLETE`，安全失败边界为 `HY3_RISK_CATEGORY_COVERAGE_INVALID`。项目保持 `failed/live`，PDF 读取仍为有效 `200`，确认没有 Mock fallback。现有代码已能让结构合法但风险类别缺失、重复或多余的响应进入有界重试，但真实供应商在空 `items` 场景仍未返回三个固定风险类别各一条；不得通过代码补齐、过滤或伪造风险结果来消除此失败。

阶段 5 可以使用冻结的阶段 4 API、Mock 夹具和稳定失败响应继续开发，但必须把以下状态作为正式界面与测试输入：

- `claims=[]`、`evidence_records=[]` 的证据不足状态；不得显示不存在的证据跳转。
- `502/AUDIT_INCOMPLETE`、项目 `failed` 和 `retryable_stage=deep_audited` 的可见失败与重试提示。
- Mock/Live 标识始终可见；Live 失败不得在前端替换为 Mock 成功。
- 非空 claims/evidence 的证据跳转使用确定性夹具完成，不得用空结果证明证据跳转已验证。

### 阶段 5：三栏工作台和证据跳转

**允许修改**：`frontend/src/`、前端测试以及实际建立 Vitest/Testing Library/Playwright 测试基础设施所需的 `frontend/package.json` 和 `frontend/package-lock.json`，不修改后端契约。

阶段 5 启动时已知 P2：阶段 3 结束时 `frontend/package.json` 虽定义 `vitest --run`，但尚未声明 Vitest 依赖且没有前端测试文件。该缺口在阶段 5 以独立依赖/测试任务修复；不得通过 `--passWithNoTests` 或删除测试脚本伪装通过，也不得在阶段 4 提前处理。

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
- `claims=[]` 和 `evidence_records=[]` 时明确显示证据不足，不渲染伪造证据或无效跳转。
- `AUDIT_INCOMPLETE` 时保留最后稳定的快速检查结果，显示完整审计失败和可重试状态，不显示总分或合格结论。
- 桌面 1440x900 和移动 390x844 下文字不重叠；移动端允许切换三个面板。

通过门槛：前三个预设流程任务通过 Playwright；前端构建通过。

### 阶段 6：修订、版本和回退

阶段 6 除实现修订、补丁接受和版本恢复外，同时实现第 4.2 节已冻结但在阶段 4 明确延后的 Markdown 导出。阶段 4 和阶段 5 不得为这些路径创建占位实现。

**进入阶段 6 的额外硬门槛**：必须先在独立的“阶段 4 Live 收口”原子任务中关闭 `LIVE-BLOCKER-01`，不得在阶段 6 修订任务中顺手修复。关闭证据必须同时包含：

1. `claims=0` 的真实 risk-only 公共 API 闭环达到 `200/deep_audited/deep_complete`，由代码生成并持久化完整 8 个维度，全程无 Mock fallback。
2. 至少一篇获授权真实文本型 PDF 产生非零 `AtomicClaim[]`、非零已验证 `EvidenceRecord[]` 和一一对应的 `SemanticJudgment[]`，并完成真实 `200/deep_complete` 审计。
3. 两条 Live 记录均只保存脱敏结构摘要和运行元数据；不保存 Key、Prompt、论文长文本、供应商原始响应或未脱敏敏感风险文本。
4. 受影响聚焦测试、后端全量、阶段 5 前端测试/构建/Playwright 和 `git diff --check` 全部通过。

任一条件未满足时，`STAGE_6_ENTRY=HOLD`。不得通过删除空 claims 场景、降低三类风险完整性、将候选证据当作已验证证据或使用 Mock 演示来绕过该门槛。

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
