import { useState } from "react";

import type {
  AuditReport,
  DeepAuditRequest,
  DimensionId,
  DisclosureStatus,
  EvidenceRecord,
  GeneratedContentLabelApplicability,
  GeneratedContentLabelStatus,
  RiskCategory,
  VersionSummary,
} from "../types";


export type AuditUiState = "idle" | "running" | "failed" | "complete";

export interface AuditUiError {
  code: string;
  message: string;
  retryable: boolean;
}

interface SidePanelProps {
  selectedSentenceId: string | null;
  selectedEvidence: EvidenceRecord | null;
  evidenceInsufficient: boolean;
  quickReport: AuditReport | null;
  deepReport: AuditReport | null;
  auditState: AuditUiState;
  auditError: AuditUiError | null;
  versions: VersionSummary[];
  canAudit: boolean;
  onRunAudit: (request: DeepAuditRequest) => void;
}


const decisionLabels = {
  qualified: "合格",
  needs_revision: "需要修改",
  unqualified: "不合格",
  pending_deep_audit: "等待完整审计",
} as const;

const dimensionLabels: Record<DimensionId, string> = {
  factual_consistency: "事实一致性",
  citation_correctness: "引文正确性",
  citation_completeness: "引文完整性",
  method_scope: "方法与范围",
  conclusion_limitations: "结论与局限",
  terminology: "术语准确性",
  reader_adaptation: "读者适配",
  risk_compliance: "风险与合规",
};

const riskCategoryLabels: Record<RiskCategory, string> = {
  sensitive_information: "敏感信息",
  author_impersonation: "作者身份冒充",
  academic_integrity: "学术诚信",
};


export function SidePanel({
  selectedSentenceId,
  selectedEvidence,
  evidenceInsufficient,
  quickReport,
  deepReport,
  auditState,
  auditError,
  versions,
  canAudit,
  onRunAudit,
}: SidePanelProps) {
  const [sourceDisclosure, setSourceDisclosure] =
    useState<DisclosureStatus>("present");
  const [aiDisclosure, setAiDisclosure] = useState<DisclosureStatus>("present");
  const [labelApplicability, setLabelApplicability] =
    useState<GeneratedContentLabelApplicability>("not_applicable");
  const [labelStatus, setLabelStatus] =
    useState<GeneratedContentLabelStatus>("not_applicable");

  const submitAudit = () => {
    onRunAudit({
      source_disclosure_status: sourceDisclosure,
      ai_assistance_disclosure_status: aiDisclosure,
      generated_content_label_applicability: labelApplicability,
      generated_content_label_status:
        labelApplicability === "not_applicable" ? "not_applicable" : labelStatus,
    });
  };

  const changeApplicability = (value: GeneratedContentLabelApplicability) => {
    setLabelApplicability(value);
    setLabelStatus(value === "applicable" ? "present" : "not_applicable");
  };

  const finalReport =
    auditState === "complete" && deepReport?.audit_status === "deep_complete"
      ? deepReport
      : null;

  return (
    <aside className="pane side-panel" aria-label="证据与审计">
      <div className="pane-heading">
        <div>
          <p className="eyebrow">EVIDENCE &amp; AUDIT</p>
          <h2>证据与审计</h2>
        </div>
        <span className="pane-meta">当前版本</span>
      </div>

      <section className="side-section" aria-labelledby="evidence-heading">
        <div className="section-heading-row">
          <h3 id="evidence-heading">句子证据</h3>
          {selectedSentenceId ? <span className="mono-label">{selectedSentenceId}</span> : null}
        </div>
        {evidenceInsufficient ? (
          <div className="inline-state inline-state--warning">
            <strong>证据不足</strong>
            <p>当前生成没有可跳转的已验证主张与证据；不会创建伪造链接。</p>
          </div>
        ) : selectedEvidence?.quote_verified &&
          selectedEvidence.page_index !== null &&
          selectedEvidence.quote ? (
          <div className="evidence-card">
            <p className="evidence-location">
              第 {selectedEvidence.page_index + 1} 页 · {selectedEvidence.block_id}
            </p>
            <blockquote>{selectedEvidence.quote}</blockquote>
            {selectedEvidence.rule_flags.length ? (
              <ul className="flag-list">
                {selectedEvidence.rule_flags.map((flag) => <li key={flag}>{flag}</li>)}
              </ul>
            ) : (
              <p className="quiet-success">引文已核验，快速规则未发现问题。</p>
            )}
          </div>
        ) : selectedSentenceId ? (
          <div className="inline-state">
            <strong>该句暂无已验证证据</strong>
            <p>仍可阅读解读，但不会触发 PDF 跳转。</p>
          </div>
        ) : (
          <div className="inline-state">
            <strong>请选择解读句子</strong>
            <p>有已验证证据的句子会同时定位 PDF 页并显示摘录。</p>
          </div>
        )}
      </section>

      <section className="side-section" aria-labelledby="quick-heading">
        <h3 id="quick-heading">快速检查</h3>
        {quickReport?.audit_status === "quick_complete" ? (
          <div className="status-line status-line--success">
            <strong>快速检查完成</strong>
            <span>等待完整审计，当前仅显示规则结果</span>
          </div>
        ) : (
          <div className="status-line">
            <strong>尚未完成</strong>
            <span>生成成功后自动运行快速规则检查</span>
          </div>
        )}
      </section>

      <section className="side-section" aria-labelledby="audit-heading">
        <h3 id="audit-heading">完整审计</h3>
        {auditState === "running" ? (
          <div className="inline-state" role="status">
            <strong>深度审计中</strong>
            <p>正在运行语义与风险检查，请保持本页打开。</p>
          </div>
        ) : null}
        {auditState === "failed" && auditError ? (
          <div className="inline-state inline-state--error" role="alert">
            <strong>完整审计失败</strong>
            <span className="error-code">{auditError.code}</span>
            <p>{auditError.message}</p>
            {quickReport ? <small>快速检查结果已保留</small> : null}
          </div>
        ) : null}
        {finalReport ? (
          <div className="deep-audit-summary">
            <div className="audit-result">
              <div>
                <span>总分</span>
                <strong>总分 {finalReport.overall_score?.toFixed(1)}</strong>
              </div>
              <span className={`decision decision--${finalReport.decision}`}>
                {decisionLabels[finalReport.decision]}
              </span>
            </div>

            <section className="audit-structure" aria-label="八维审计结果">
              <h4>八维结果</h4>
              <div className="audit-dimension-list">
                {finalReport.dimensions.map((dimension) => (
                  <div className="audit-dimension-row" key={dimension.dimension_id}>
                    <strong>{dimensionLabels[dimension.dimension_id]}</strong>
                    <span className="audit-row-values">
                      <span>分数 {dimension.score.toFixed(1)}</span>
                      <span>等级 {dimension.level}</span>
                    </span>
                  </div>
                ))}
              </div>
            </section>

            {finalReport.risk_assessment ? (
              <section className="audit-structure" aria-label="结构化风险状态">
                <h4>结构化风险</h4>
                <div className="risk-status-list">
                  {finalReport.risk_assessment.risk_findings.map((finding) => (
                    <div className="risk-status-row" key={finding.category}>
                      <strong>{riskCategoryLabels[finding.category]}</strong>
                      <span>{finding.status}</span>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        ) : null}

        {!finalReport ? (
          <div className="audit-form">
            <label>
              来源说明
              <select
                value={sourceDisclosure}
                onChange={(event) =>
                  setSourceDisclosure(event.target.value as DisclosureStatus)
                }
              >
                <option value="present">已提供</option>
                <option value="missing">缺失</option>
              </select>
            </label>
            <label>
              AI 辅助说明
              <select
                value={aiDisclosure}
                onChange={(event) => setAiDisclosure(event.target.value as DisclosureStatus)}
              >
                <option value="present">已提供</option>
                <option value="missing">缺失</option>
              </select>
            </label>
            <label>
              生成内容标识
              <select
                value={labelApplicability}
                onChange={(event) =>
                  changeApplicability(
                    event.target.value as GeneratedContentLabelApplicability,
                  )
                }
              >
                <option value="not_applicable">不适用</option>
                <option value="applicable">适用</option>
              </select>
            </label>
            {labelApplicability === "applicable" ? (
              <label>
                标识状态
                <select
                  value={labelStatus}
                  onChange={(event) =>
                    setLabelStatus(event.target.value as GeneratedContentLabelStatus)
                  }
                >
                  <option value="present">已提供</option>
                  <option value="missing">缺失</option>
                </select>
              </label>
            ) : null}
            <button
              className="primary-button"
              type="button"
              disabled={!canAudit || auditState === "running"}
              onClick={submitAudit}
            >
              {auditState === "running"
                ? "深度审计中…"
                : auditState === "failed" && auditError?.retryable
                  ? "重试完整审计"
                  : "运行完整审计"}
            </button>
          </div>
        ) : null}
      </section>

      <section className="side-section" aria-labelledby="revision-heading">
        <div className="section-heading-row">
          <h3 id="revision-heading">修改</h3>
          <span className="muted-badge">阶段 6 尚未开放</span>
        </div>
        <button type="button" className="secondary-button" disabled>
          讨论并生成修改
        </button>
      </section>

      <section className="side-section" aria-labelledby="version-heading">
        <div className="section-heading-row">
          <h3 id="version-heading">版本历史</h3>
          <span className="pane-meta">已读取 {versions.length} 个版本</span>
        </div>
        {versions.length ? (
          <ol className="version-list">
            {versions.map((version) => (
              <li key={version.version_id}>
                <div>
                  <strong>版本 {version.version_no}</strong>
                  <span>{version.reason}</span>
                </div>
                <button type="button" disabled title="阶段 6 才能恢复版本">
                  恢复
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted-text">生成首个版本后显示历史记录。</p>
        )}
      </section>
    </aside>
  );
}
