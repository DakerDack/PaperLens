GENERATION_PROMPT_VERSION = "gen-v1"
GENERATION_SCHEMA_VERSION = "generated-bundle-v1"
GENERATION_SCHEMA_NAME = "paperlens_generated_bundle_v1"


COMMON_SYSTEM_PROMPT = """你是 PaperLens 的受约束学术内容处理模块。
你只能依据输入中的 SourceBlock，不得使用外部知识补充事实。
不得创建输入中不存在的 block_id、页码、引文、数字或研究结论。
证据不足时必须输出 insufficient 或空候选，不得猜测。
必须严格遵守给定 JSON Schema，不得输出 Markdown 代码块或额外说明。"""


GENERATION_USER_PROMPT_TEMPLATE = """任务：面向本科生生成一份可核验论文解读，并同步给出每个句子的原子主张候选。

输入：
- paper_metadata: {paper_metadata_json}
- source_blocks: {source_blocks_json}

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


def render_generation_prompt(
    *,
    paper_metadata_json: str,
    source_blocks_json: str,
) -> str:
    return GENERATION_USER_PROMPT_TEMPLATE.format(
        paper_metadata_json=paper_metadata_json,
        source_blocks_json=source_blocks_json,
    )
