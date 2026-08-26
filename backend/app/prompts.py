from backend.app.models import ClaimPolicy


GENERATION_PROMPT_VERSION = "gen-v3"
GENERATION_SCHEMA_VERSION = "generated-bundle-v1"
GENERATION_SCHEMA_NAME = "paperlens_generated_bundle_v1"
DEEP_AUDIT_PROMPT_VERSION = "audit-v2"
DEEP_AUDIT_SCHEMA_VERSION = "deep-audit-result-v2"
DEEP_AUDIT_SCHEMA_NAME = "paperlens_deep_audit_result_v2"


COMMON_SYSTEM_PROMPT = """你是 PaperLens 的受约束学术内容处理模块。
你只能依据输入中的 SourceBlock，不得使用外部知识补充事实。
不得创建输入中不存在的 block_id、页码、引文、数字或研究结论。
证据不足时必须输出 insufficient 或空候选，不得猜测。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


DEEP_AUDIT_SYSTEM_PROMPT = """你是 PaperLens 的受约束深度审计模块。
事实判断只能依据输入中的已验证 claim/evidence 配对，不得使用外部知识补充证据。
文档风险检查只能检查输入中的完整 ContentDraft，不得使用 SourceBlock 推断风险位置。
不得生成或推断页码、bbox、最终风险等级、分数、权重、硬失败或合格结论。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


GENERATION_USER_PROMPT_TEMPLATE = """任务：面向本科生生成一份可核验论文解读，并同步给出每个句子的原子主张候选。

输入：
- claim_policy: {claim_policy}
- paper_metadata: {paper_metadata_json}
- source_blocks: {source_blocks_json}

claim_policy=required 时，claims 必须至少包含 1 项。
claim_policy=must_be_empty 时，claims 必须严格为空数组；不得生成、删除或隐藏任何 claim。

document.sections 固定为以下五区，且每个 section_id 恰好出现一次：
- research_question：研究问题
- methods：方法与数据
- results：主要结果
- limitations：限制条件
- plain_explanation：通俗解释

每句话必须有稳定且唯一的 sentence_id。
每条 AtomicClaim 只表达一个可独立判断真假的事实，必须关联存在的 sentence_id，并使用唯一 claim_id。
主张中的数字、样本、条件、比较对象和结论范围不得省略。
candidate_block_ids 只能从输入选择，最多 3 个；它们只是尚未核验的候选来源。
candidate_quote 必须复制候选来源块中的连续文本，或只对空白做规范化；它只是候选引文，不是已验证证据。
建议、修辞和主观说明标记为 non_auditable；证据不足时使用空候选，不得猜测。
不得生成页码、bbox、总分或合格结论。

输出：严格符合 GeneratedBundle JSON Schema 的单个 JSON 对象。"""


DEEP_AUDIT_USER_PROMPT_TEMPLATE = """任务一：逐条判断 claim 是否被给定 evidence 支持。

输入：
- document: {content_draft_json}
- items: {verified_claim_evidence_pairs_json}

每个输出必须保留对应输入的 claim_id 和 evidence.block_id，且每个输入项恰好返回一次判断。
不得参考分数、预设质量档位、攻击标签或其他 claim 的最终判断。
重点检查事实关系、相关性与因果、样本和适用范围、术语语境、关键限定条件。
证据不能直接支持时选择 insufficient，不得依靠常识补足。
不得补充给定 evidence 之外的知识或证据，不得返回页码或 bbox。
即使 items 为空，也必须继续执行任务二。

任务二：检查完整生成文档中的 sensitive_information、author_impersonation 和 academic_integrity。
每个类别恰好返回一条 RiskFinding；non_auditable 句子也必须检查。
RiskLocation 只能定位完整 ContentDraft 的 sentence、title 或 document，不得引用 SourceBlock。
detected 必须至少给出一个合法位置；not_detected 的 locations 必须为空；unclear 可为空。
非空 evidence_excerpt 最长 160 字符且必须原样摘自对应标题或句子。
sensitive_information 的 evidence_excerpt 必须为 null，reason 和 remediation 不得复述完整敏感值。
不得判断许可、来源披露、AI 辅助披露或生成内容标识是否适用和存在。
不得返回风险等级、分数、权重、硬失败、decision、页码或 bbox。

输出：严格符合 DeepAuditResult v2 JSON Schema 的单个 JSON 对象。"""


def render_generation_prompt(
    *,
    claim_policy: ClaimPolicy,
    paper_metadata_json: str,
    source_blocks_json: str,
) -> str:
    return GENERATION_USER_PROMPT_TEMPLATE.format(
        claim_policy=claim_policy,
        paper_metadata_json=paper_metadata_json,
        source_blocks_json=source_blocks_json,
    )


def render_generation_retry_prompt(
    *,
    validation_summaries: list[tuple[int, str]],
) -> str:
    lines = [
        "字段错误摘要：以下为此前 attempts 已发现的全部安全契约错误。"
        "本次必须同时修复全部历史契约错误，不得在修复新错误时"
        "重新违反先前约束。"
    ]
    lines.extend(
        f"- attempt={attempt}: {summary}"
        for attempt, summary in validation_summaries
    )
    return "\n".join(lines)


def render_deep_audit_prompt(
    *,
    content_draft_json: str,
    verified_claim_evidence_pairs_json: str,
) -> str:
    return DEEP_AUDIT_USER_PROMPT_TEMPLATE.format(
        content_draft_json=content_draft_json,
        verified_claim_evidence_pairs_json=(
            verified_claim_evidence_pairs_json
        ),
    )
