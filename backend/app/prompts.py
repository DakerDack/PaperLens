import json

from backend.app.models import ClaimPolicy


GENERATION_PROMPT_VERSION = "gen-v4"


def render_scope_context(context: list[dict]) -> str:
    return (
        "\n范围来源补充（每请求共享一次，独立于原引用）：\nverified_scope_context: "
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        + "\n仅将明确关联的研究设计、研究对象和抽样范围用于对应结果；来源位置及原文经代码核验，"
        "不代表总体代表性或语义支持已获确认。修订及重建保留同一范围边界，不能把上下文冒充原引用。"
        "空列表表示缺失，非完整性保证；缺失或不适用时不得默认支持总体推广或 preserved，"
        "沿用证据不足/无法判断及既有严重度契约。不得从其他文档句子补齐范围。\n"
    )
GENERATION_SCHEMA_VERSION = "generated-bundle-v1"
GENERATION_SCHEMA_NAME = "paperlens_generated_bundle_v1"
DEEP_AUDIT_PROMPT_VERSION = "audit-v9"
DEEP_AUDIT_SCHEMA_VERSION = "deep-audit-result-v3"
DEEP_AUDIT_SCHEMA_NAME = "paperlens_deep_audit_result_v3"
REVISION_PROMPT_VERSION = "revision-v3"
REVISION_SCHEMA_VERSION = "edit-patch-v1"
REVISION_SCHEMA_NAME = "paperlens_edit_patch_v1"
CLAIM_REGENERATION_PROMPT_VERSION = "claim-regen-v1"
SENTENCE_CLAIMS_PROMPT_VERSION = "sentence-claims-v3"
SENTENCE_CLAIMS_SCHEMA_VERSION = "sentence-claims-result-v1"
SENTENCE_CLAIMS_SCHEMA_NAME = "paperlens_sentence_claims_v1"


COMMON_SYSTEM_PROMPT = """你是 PaperLens 的受约束学术内容处理模块。
你只能依据输入中的 SourceBlock，不得使用外部知识补充事实。
不得创建输入中不存在的 block_id、页码、引文、数字或研究结论。
证据不足时必须输出 insufficient 或空候选，不得猜测。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


DEEP_AUDIT_SYSTEM_PROMPT = """你是 PaperLens 的受约束深度审计模块。
事实判断依据输入中的已验证 claim/evidence 配对及明确关联的 verified_scope_context，不得使用外部知识补充证据。
文档风险检查只能检查输入中的完整 ContentDraft，不得使用 SourceBlock 推断风险位置。
不得生成或推断页码、bbox、最终风险等级、分数、权重、硬失败或合格结论。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


REVISION_SYSTEM_PROMPT = """你是 PaperLens 的受约束修订模块。
你只能根据当前目标、已验证证据、明确关联的 verified_scope_context 和用户意图生成一个待确认 EditPatch。
不得直接应用修改，不得引用历史版本，不得生成页码、bbox、分数或合格结论。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


SENTENCE_CLAIMS_SYSTEM_PROMPT = """你是 PaperLens 的受约束目标句主张重建模块。
你只能依据输入中的已接受目标句、原目标主张、相关已验证证据、明确关联的 verified_scope_context 和允许的 block_id，不得使用外部知识。
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

语义配对输入区域开始
- items: {verified_claim_evidence_pairs_json}
语义配对输入区域结束

每个输出必须保留对应输入的 claim_id 和 evidence.block_id，且每个输入项恰好返回一次判断。
不得参考分数、预设质量档位、攻击标签或其他 claim 的最终判断。
任务一的局部事实与范围判断只能使用当前 item 的 claim 和 evidence，以及 applies_to_block_ids 明确关联该 evidence 的 verified_scope_context；完整 document 仅供任务二文档风险及任务三表达检查，不得为当前配对补充边界或借入其他句子的问题。
不得根据其他句子的问题数量、文档整体质量、其他配对或排列顺序改变当前 relation、scope_status、terminology_status、severity。
判 expanded 前，必须能在当前 claim/evidence 中指出本断言实际丢失或改变的必要条件、数值精度或比较基准；不能用文档级印象代替局部证据。
逐项检查顺序：
1. 先判断 claim 中保留的事实是否被 evidence 支持；再独立检查相关边界或精度是否丢失。
2. 只核对与当前断言的同一对象、谓词、结果或比较关系直接相关的数值精度和比较基准。数值精度遗漏包括相关数量或效应量的精确程度丢失；比较基准遗漏包括相对对象或参照条件丢失。不得把 evidence 中出现但 claim 未重复的所有数字、条件都判为遗漏。
比较关系仍存在不等于比较对象完整；higher、longer 等方向词本身不能确定参照对象。若明确比较对象只能从 evidence 补入、无法从 claim 自身唯一恢复，应单独报告该必要比较边界损失，事实支持与严重度仍按现有规则分别判断。若 claim 自身已通过等价表达或指向明确先行对象的指代唯一确定参照，则不因省略比较连接词或未重复背景而判 expanded。
3. 核心事实仍被支持，且相关精度或比较边界仅有局部损失、未实质改变主张理解时，可以使用 relation=supports + scope_status=expanded + severity=minor。不得仅因省略细节就自动判 contradicts、insufficient、major、critical。
保留完整边界的同义改述、语序变化或合法简写不应误报；与当前断言无关的背景事实可以省略。
真正的数值错误、方向反转、因果改变仍按事实与严重度契约处理，不得统一降为 minor。
检查步骤仅为内部指令，不新增输出字段。
合成对照：
- 数值精度遗漏：证据“装置比基准耗能低 18%”；断言“装置比基准耗能低”——仅丢失幅度且不改变结论时为 supports/expanded/minor。
- 比较基准遗漏：证据“传感器比标准探头更灵敏”；断言“传感器更灵敏”——仅丢失比较对象且未引入更强结论时为 supports/expanded/minor。
- 完整同义改述：证据“与标准探头相比，传感器灵敏度更高”；断言“传感器比标准探头更灵敏”——supports/preserved/none。
- 无关背景省略：证据“记录仪外壳为绿色。阀门在 8 秒后关闭”；断言“阀门在 8 秒后关闭”——supports/preserved/none。
- 局部限制对照：证据“该结论仅适用于密封容器中的样本”；断言“结论只适用于密封容器中的样本”——本配对支持且边界完整时为 supports/preserved/none；其他句子从“指示灯为蓝色”变为“指示灯为红色”不改变此判断。
- 必要条件遗漏：同一证据下，断言“结论适用于容器中的样本”丢失了密封条件，应标 scope_status=expanded；relation 与 severity 按本配对的实际影响判定，不得沿用前例的 preserved/none。

语义判定固定规则：
- relation=supports：仅当 evidence 直接蕴含 claim 的全部实质事实时选择。
- relation=contradicts：当数字、方向、因果、比较或结论冲突时选择。
- relation=insufficient：仅当给定 evidence 缺少对 claim 的直接支持时选择。当前 evidence 已直接支持时，不得额外要求背景、其他 SourceBlock 或外部知识。
- scope_status=preserved：只有样本、方法、比较对象、条件、数量、适用性、因果强度和限定语均未被扩大时选择。缩短表述但仍保留全部边界时仍为 preserved。
- scope_status=expanded：省略或改变比较对象、实验条件、总体范围、因果边界、数值或效应量而使 claim 更宽时选择。relation=supports 与 scope_status=expanded 可以同时成立。
- terminology_status=correct：同义改述或不同措辞但语义相同仍为 correct；只有替换、泛化或改变含义时才选择 misused，不得仅因措辞不同选择 unclear 或 misused。
- 只有该 claim/evidence 配对本身无法判断时才选择 scope_status=unclear 或 terminology_status=unclear。直接可判定的配对不得以 unclear 作为回避或兜底选择。
- severity=none：配对不存在事实、范围或术语问题。
- severity=minor：存在局部精度或限定语损失，但不实质改变研究对象、比较条件、数值或效应解释、因果强度、方向或主要结论。
- severity=major：错误会实质改变对一个主张的理解，例如重要范围、因果、数值、比较或方向发生变化，但尚未推翻核心或关键结论。
- severity=critical：仅当问题足以反转、伪造或使核心或关键结论实质错误时选择。不得仅因措辞不同、claim 的 importance 标为 critical 或普通范围缺失而选择 severity=critical。
- scope_status=expanded 或 terminology_status=misused 时，不得在无充分理由下选择 severity=none。不得为了区分标签而虚构问题。
- 限制性主张须核对其自身限制的对象及必要边界；只有本配对直接支持且无事实、范围或术语问题时才判 supports/preserved/none，不得因它是限制句就预设通过。
- 当前绑定 evidence 已明确支持该限制性主张及其必要边界时，应选择 relation=supports；不得额外要求背景或其他 SourceBlock。
- 同一未变的 claim/evidence 配对必须给出相同判断，不得受其他 items、顺序或无关文档内容影响。
除明确关联的 verified_scope_context 外，不得补充给定 evidence 之外的知识或证据，不得返回页码或 bbox。
即使 items 为空，也必须继续执行任务二。
items 为空时，semantic_judgments 必须为空数组；不得为不存在的配对生成判断。

任务二：检查完整生成文档中的 sensitive_information、author_impersonation 和 academic_integrity。
文档风险输入区域开始
- document: {content_draft_json}
文档风险输入区域结束

每个类别恰好返回一条 RiskFinding；non_auditable 句子也必须检查。
risk_findings 必须仍为长度恰好为 3 的数组，并使用以下固定顺序：
- risk_findings[0].category 必须为 sensitive_information
- risk_findings[1].category 必须为 author_impersonation
- risk_findings[2].category 必须为 academic_integrity
禁止缺失、重复、增加类别或返回空 risk_findings。

任务三：独立检查完整 document（包括没有 claim 的句子）的表达，不改变任务一事实配对和任务二三类风险判断。
expression_findings 必填且恰好两项，每类一次；每项仅有 category、status、locations：
- redundancy_or_off_topic：没有信息增量的重复或偏离当前主题、影响表达组织。
- unexplained_terminology：影响理解且当前上下文没有解释的术语；术语含义错误仍由任务一 terminology_status 判断。
status 必须明确选择 not_detected、detected 或 unclear。无法判断使用 unclear，不得猜测为未发现。
detected 必须提供至少一个句子位置；每个位置只含 location_type=sentence、当前文档实际存在的 sentence_id、与该句匹配的非空短摘录 evidence_excerpt（最多 160 字符）。不得用 document 或 title 位置。
not_detected 和 unclear 的 locations 必须为空。不返回 reason、severity、分数或完整覆盖句子 ID 清单。
必要复述、上下文已解释的术语、合法长句不能仅因长度或关键词判为问题；检查信息增量、主题关联和理解所需解释。
即使 items 为空也必须完成两类表达检查；只定位实际发现的问题，不为凑覆盖制造位置。
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

输出：严格符合 DeepAuditResult v3 JSON Schema 的单个 JSON 对象。"""


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
