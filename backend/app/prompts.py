from backend.app.models import ClaimPolicy


GENERATION_PROMPT_VERSION = "gen-v4"
GENERATION_SCHEMA_VERSION = "generated-bundle-v1"
GENERATION_SCHEMA_NAME = "paperlens_generated_bundle_v1"
DEEP_AUDIT_PROMPT_VERSION = "audit-v2"
DEEP_AUDIT_SCHEMA_VERSION = "deep-audit-result-v2"
DEEP_AUDIT_SCHEMA_NAME = "paperlens_deep_audit_result_v2"
REVISION_PROMPT_VERSION = "revision-v2"
REVISION_SCHEMA_VERSION = "edit-patch-v1"
REVISION_SCHEMA_NAME = "paperlens_edit_patch_v1"
CLAIM_REGENERATION_PROMPT_VERSION = "claim-regen-v1"
SENTENCE_CLAIMS_PROMPT_VERSION = "sentence-claims-v2"
SENTENCE_CLAIMS_SCHEMA_VERSION = "sentence-claims-result-v1"
SENTENCE_CLAIMS_SCHEMA_NAME = "paperlens_sentence_claims_v1"


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


REVISION_SYSTEM_PROMPT = """你是 PaperLens 的受约束修订模块。
你只能根据当前目标、已验证证据和用户意图生成一个待确认 EditPatch。
不得直接应用修改，不得引用历史版本，不得生成页码、bbox、分数或合格结论。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


SENTENCE_CLAIMS_SYSTEM_PROMPT = """你是 PaperLens 的受约束目标句主张重建模块。
你只能依据输入中的已接受目标句、原目标主张、相关已验证证据和允许的 block_id，不得使用外部知识。
不得生成页码、bbox、verified 状态、分数、最终判断或完整文档。
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

所有五区中的 sentence_id 必须全局唯一，不得在不同 section 中重复。
每条 AtomicClaim 只表达一个可独立判断真假的事实，必须关联存在的 sentence_id，并使用唯一 claim_id。
主张中的数字、样本、条件、比较对象和结论范围不得省略。
candidate_block_ids 只能从输入选择，最多 3 个；auditability=auditable 时必须包含 1 至 3 个候选。
candidate_quote 必须为 null 或非空字符串；非空时必须复制候选来源块中的连续文本，或只对空白做规范化；它只是候选引文，不是已验证证据。
证据不足并使用空候选时，不得标记为 auditable；应根据主张性质标记为 needs_review 或 non_auditable，不得猜测。
建议、修辞和主观说明标记为 non_auditable。
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
items 为空时，semantic_judgments 必须为空数组；不得为不存在的配对生成判断。

任务二：检查完整生成文档中的 sensitive_information、author_impersonation 和 academic_integrity。
每个类别恰好返回一条 RiskFinding；non_auditable 句子也必须检查。
risk_findings 必须仍为长度恰好为 3 的数组，并使用以下固定顺序：
- risk_findings[0].category 必须为 sensitive_information
- risk_findings[1].category 必须为 author_impersonation
- risk_findings[2].category 必须为 academic_integrity
禁止缺失、重复、增加类别或返回空 risk_findings。
每个 RiskFinding 对象只能且必须包含 category、status、locations、reason、remediation。
每个 RiskLocation 对象只能且必须包含 location_type、sentence_id、evidence_excerpt。
evidence_excerpt 只能存在于 locations 数组的 RiskLocation 对象中；evidence_excerpt 不得成为 RiskFinding 顶层字段。
reason 和 remediation 只属于 RiskFinding，不属于 RiskLocation。
禁止返回任何未列出的键。
RiskLocation 只能定位完整 ContentDraft 的 sentence、title 或 document，不得引用 SourceBlock。
detected 必须至少给出一个合法位置；not_detected 的 locations 必须为空；unclear 可为空。
非空 evidence_excerpt 最长 160 字符且必须原样摘自对应标题或句子。
sensitive_information 的 evidence_excerpt 必须为 null，reason 和 remediation 不得复述完整敏感值。
不得判断许可、来源披露、AI 辅助披露或生成内容标识是否适用和存在。
不得返回风险等级、分数、权重、硬失败、decision、页码或 bbox。

输出：严格符合 DeepAuditResult v2 JSON Schema 的单个 JSON 对象。"""


SENTENCE_REVISION_USER_PROMPT_TEMPLATE = """任务：根据用户意图生成一个句子级待确认 EditPatch，不直接修改文档。

输入：
- patch_id: {patch_id_json}
- base_version: {base_version}
- scope: sentence
- target_sentence_id: {sentence_id_json}
- current_text: {current_text_json}
- before_hash: {before_hash}
- related_verified_evidence: {verified_evidence_json}
- user_instruction: {user_instruction_json}

只能修改 target_sentence_id 指向的当前句子。after_text 必须是单个非空句子，不得包含其他句子、标题、章节或全文。
patch_id 必须逐字符原样回显输入值，不得自行生成、替换或省略。
before_text 必须与 current_text 完全相同；before_hash、base_version、scope 和 target_sentence_ids 必须原样保留。
style_change 时锁定数字、限定条件、事实关系和证据。
content_change 时必须准确声明 fact_changed 和 evidence_changed。
不得删除原文明确给出的关键限制，不得生成新引用。

输出：严格符合 EditPatch JSON Schema 的单个 JSON 对象。"""


DOCUMENT_REVISION_USER_PROMPT_TEMPLATE = """任务：根据用户意图生成一个全文待确认 EditPatch，不直接修改文档。

输入：
- patch_id: {patch_id_json}
- base_version: {base_version}
- scope: document
- current_document: {content_draft_json}
- before_hash: {before_hash}
- user_instruction: {user_instruction_json}

输入只包含当前五区 ContentDraft，不包含任何历史版本。
after_text 必须是修订后 ContentDraft 的严格 JSON 字符串，并保留五个固定 section_id 各一次。
patch_id 必须逐字符原样回显输入值，不得自行生成、替换或省略。
before_text 必须与 current_document 的紧凑 JSON 完全相同；before_hash、base_version 和 scope 必须原样保留，target_sentence_ids 必须为空数组。
不得删除原文明确给出的关键限制，不得生成新引用。

输出：严格符合 EditPatch JSON Schema 的单个 JSON 对象。"""


CLAIM_REGENERATION_USER_PROMPT_TEMPLATE = """任务：只为已经由用户接受的当前五区文档重新生成原子主张候选。

输入：
- current_document: {content_draft_json}
- source_blocks: {source_blocks_json}

输出中的 document 必须逐字段原样返回 current_document，不得改写标题、句子、section_id 或 sentence_id。
claims 必须至少包含 1 项；每条主张只能关联 current_document 中存在的 sentence_id。
每条 AtomicClaim 只表达一个可独立判断真假的事实，并使用唯一 claim_id。
candidate_block_ids 只能从输入 source_blocks 选择，最多 3 个；证据不足时不得猜测。
candidate_quote 必须为 null 或来源块中的连续原文，只是候选引文，不是已验证证据。
不得使用历史版本，不得生成页码、bbox、分数或合格结论。

输出：严格符合 GeneratedBundle JSON Schema 的单个 JSON 对象。"""


SENTENCE_CLAIMS_USER_PROMPT_TEMPLATE = """任务：只为已经由用户接受的目标句重新生成原子主张候选。

输入：
- target_sentence_id: {target_sentence_id_json}
- accepted_after_text: {accepted_after_text_json}
- original_target_claims: {original_claims_json}
- related_verified_evidence: {verified_evidence_json}
- allowed_candidate_block_ids: {allowed_block_ids_json}
- auditable_claim_required: {auditable_claim_required_json}

每条输出 claim 的 sentence_id 必须严格等于 target_sentence_id，claim_id 必须互不重复。
candidate_block_ids 只能从 allowed_candidate_block_ids 选择，不得增加、替换或猜测 block_id。
auditable_claim_required=true 时，输出必须至少包含一条 auditability=auditable 的 claim，不得只用 non_auditable 或 needs_review 满足最少一条要求。
每条 AtomicClaim 只表达一个可独立判断真假的事实；数字、样本、条件、比较对象和结论范围不得省略。
candidate_quote 只是候选引文，不是已验证证据；证据不足时不得猜测。
不得返回文档、其他句子、历史版本、页码、bbox、verified 状态、分数或最终结论。

输出：严格符合 SentenceClaimRegenerationResult JSON Schema 的单个 JSON 对象。"""


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


def render_sentence_revision_prompt(
    *,
    patch_id_json: str,
    base_version: int,
    sentence_id_json: str,
    current_text_json: str,
    before_hash: str,
    verified_evidence_json: str,
    user_instruction_json: str,
) -> str:
    return SENTENCE_REVISION_USER_PROMPT_TEMPLATE.format(
        patch_id_json=patch_id_json,
        base_version=base_version,
        sentence_id_json=sentence_id_json,
        current_text_json=current_text_json,
        before_hash=before_hash,
        verified_evidence_json=verified_evidence_json,
        user_instruction_json=user_instruction_json,
    )


def render_document_revision_prompt(
    *,
    patch_id_json: str,
    base_version: int,
    content_draft_json: str,
    before_hash: str,
    user_instruction_json: str,
) -> str:
    return DOCUMENT_REVISION_USER_PROMPT_TEMPLATE.format(
        patch_id_json=patch_id_json,
        base_version=base_version,
        content_draft_json=content_draft_json,
        before_hash=before_hash,
        user_instruction_json=user_instruction_json,
    )


def render_claim_regeneration_prompt(
    *,
    content_draft_json: str,
    source_blocks_json: str,
) -> str:
    return CLAIM_REGENERATION_USER_PROMPT_TEMPLATE.format(
        content_draft_json=content_draft_json,
        source_blocks_json=source_blocks_json,
    )


def render_sentence_claim_regeneration_prompt(
    *,
    target_sentence_id_json: str,
    accepted_after_text_json: str,
    original_claims_json: str,
    verified_evidence_json: str,
    allowed_block_ids_json: str,
    auditable_claim_required_json: str,
) -> str:
    return SENTENCE_CLAIMS_USER_PROMPT_TEMPLATE.format(
        target_sentence_id_json=target_sentence_id_json,
        accepted_after_text_json=accepted_after_text_json,
        original_claims_json=original_claims_json,
        verified_evidence_json=verified_evidence_json,
        allowed_block_ids_json=allowed_block_ids_json,
        auditable_claim_required_json=auditable_claim_required_json,
    )
