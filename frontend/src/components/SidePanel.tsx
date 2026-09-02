import { useState } from "react";

import type {
  AuditReport,
  DeepAuditRequest,
  DimensionId,
  DisclosureStatus,
  EditPatch,
  EvidenceRecord,
  GeneratedContentLabelApplicability,
  GeneratedContentLabelStatus,
  PatchScope,
  RiskCategory,
  VersionSummary,
} from "../types";


export type AuditUiState = "idle" | "running" | "failed" | "complete";
export type RevisionUiState =
  | "idle"
  | "previewing"
  | "preview_ready"
  | "accepting"
  | "rejecting"
  | "restoring"
  | "refreshing"
  | "write_uncertain"
  | "reconciling"
  | "reconcile_failed"
  | "exporting"
  | "refresh_failed"
  | "failed";

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
  currentVersionId?: string | null;
  pendingPatch?: EditPatch | null;
  revisionState?: RevisionUiState;
  revisionError?: AuditUiError | null;
  revisionRefreshReason?: "completed_write" | "target_stale" | null;
  onCreateRevision?: (input: {
    scope: PatchScope;
    userInstruction: string;
  }) => void;
  onAcceptRevision?: () => void;
  onRejectRevision?: () => void;
  onRetryRevision?: () => void;
  onRestoreVersion?: (versionId: string) => void;
  onExportProject?: () => void;
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
  currentVersionId = null,
  pendingPatch = null,
  revisionState = "idle",
  revisionError = null,
  revisionRefreshReason = null,
  onCreateRevision,
  onAcceptRevision,
  onRejectRevision,
  onRetryRevision,
  onRestoreVersion,
  onExportProject,
}: SidePanelProps) {
  const [sourceDisclosure, setSourceDisclosure] =
    useState<DisclosureStatus>("present");
  const [aiDisclosure, setAiDisclosure] = useState<DisclosureStatus>("present");
  const [labelApplicability, setLabelApplicability] =
    useState<GeneratedContentLabelApplicability>("not_applicable");
  const [labelStatus, setLabelStatus] =
    useState<GeneratedContentLabelStatus>("not_applicable");
  const [revisionScope, setRevisionScope] = useState<PatchScope>("sentence");
  const [revisionInstruction, setRevisionInstruction] = useState("");

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
  const revisionBusy = [
    "previewing",
    "accepting",
    "rejecting",
    "restoring",
    "refreshing",
    "reconciling",
    "exporting",
  ].includes(revisionState);
  const auditBusy = auditState === "running";
  const snapshotBlocked = [
    "write_uncertain",
    "reconciling",
    "reconcile_failed",
    "refresh_failed",
  ].includes(revisionState);
  const canPreviewRevision = Boolean(
    currentVersionId &&
      onCreateRevision &&
      revisionInstruction.trim() &&
      (revisionScope === "document" || selectedSentenceId) &&
      !revisionBusy &&
      !auditBusy &&
      !snapshotBlocked &&
      !pendingPatch,
  );
  const revisionProgressLabel = {
    previewing: "正在生成补丁预览…",
    accepting: "正在接受补丁并重新检查…",
    rejecting: "正在拒绝补丁…",
    restoring: "正在复制历史快照并回退…",
    refreshing: "正在刷新当前版本…",
    reconciling: "正在读取服务端当前版本…",
    exporting: "正在导出当前版本…",
  }[
    revisionState as
      | "previewing"
      | "accepting"
      | "rejecting"
      | "restoring"
      | "refreshing"
      | "reconciling"
      | "exporting"
  ];

  const displayPatchText = (text: string) => {
    if (pendingPatch?.scope !== "document") {
      return text;
    }
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text;
    }
  };

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
                disabled={auditBusy || revisionBusy || snapshotBlocked}
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
                disabled={auditBusy || revisionBusy || snapshotBlocked}
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
                disabled={auditBusy || revisionBusy || snapshotBlocked}
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
                  disabled={auditBusy || revisionBusy || snapshotBlocked}
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
              disabled={!canAudit || auditBusy || revisionBusy || snapshotBlocked}
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
          <span className="muted-badge">
            {selectedSentenceId ? `已选 ${selectedSentenceId}` : "可修改全文"}
          </span>
        </div>
        <div className="audit-form">
          <label>
            修改范围
            <select
              value={revisionScope}
              disabled={
                revisionBusy || auditBusy || snapshotBlocked || Boolean(pendingPatch)
              }
              onChange={(event) => setRevisionScope(event.target.value as PatchScope)}
            >
              <option value="sentence" disabled={!selectedSentenceId}>
                当前句子
              </option>
              <option value="document">全文五区</option>
            </select>
          </label>
          <label>
            修改意图
            <textarea
              value={revisionInstruction}
              disabled={
                revisionBusy || auditBusy || snapshotBlocked || Boolean(pendingPatch)
              }
              placeholder="例如：保持事实不变，缩短句子并突出限制条件"
              onChange={(event) => setRevisionInstruction(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="secondary-button"
            disabled={!canPreviewRevision}
            onClick={() =>
              onCreateRevision?.({
                scope: revisionScope,
                userInstruction: revisionInstruction.trim(),
              })
            }
          >
            生成补丁预览
          </button>
        </div>

        {revisionProgressLabel ? (
          <div className="inline-state" role="status">
            <strong>{revisionProgressLabel}</strong>
            <p>
              {revisionState === "refreshing"
                ? revisionRefreshReason === "target_stale"
                  ? "当前修订目标已过期，正在读取服务端当前版本。"
                  : "写操作已完成，正在读取服务端当前版本。"
                : revisionState === "reconciling"
                  ? "写入结果尚未确认，需要读取服务端当前版本。"
                : "当前稳定版本不会在操作完成前被覆盖。"}
            </p>
          </div>
        ) : null}
        {(revisionState === "failed" ||
          revisionState === "refresh_failed" ||
          revisionState === "write_uncertain" ||
          revisionState === "reconcile_failed") &&
        revisionError ? (
          <div className="inline-state inline-state--error" role="alert">
            <strong>
              {revisionState === "refresh_failed"
                ? "当前版本刷新失败"
                : revisionState === "write_uncertain" ||
                    revisionState === "reconcile_failed"
                  ? "写入结果尚未确认"
                  : "修改操作失败"}
            </strong>
            <span className="error-code">{revisionError.code}</span>
            <p>{revisionError.message}</p>
            {revisionError.retryable ? (
              <button
                type="button"
                disabled={auditBusy || revisionBusy}
                onClick={() => onRetryRevision?.()}
              >
                {revisionState === "write_uncertain" ||
                revisionState === "reconcile_failed"
                  ? "读取服务端当前版本"
                  : "重试修改操作"}
              </button>
            ) : null}
          </div>
        ) : null}
        {pendingPatch ? (
          <section className="audit-structure" aria-label="修改前后差异">
            <h4>{pendingPatch.scope === "sentence" ? "句子补丁预览" : "全文补丁预览"}</h4>
            <p>{pendingPatch.reason}</p>
            <div className="inline-state">
              <strong>修改前</strong>
              <pre>{displayPatchText(pendingPatch.before_text)}</pre>
            </div>
            <div className="inline-state inline-state--warning">
              <strong>修改后</strong>
              <pre>{displayPatchText(pendingPatch.after_text)}</pre>
            </div>
            <div className="audit-form">
              <button
                type="button"
                className="primary-button"
                disabled={revisionBusy || auditBusy || snapshotBlocked}
                onClick={() => onAcceptRevision?.()}
              >
                接受修改
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={revisionBusy || auditBusy || snapshotBlocked}
                onClick={() => onRejectRevision?.()}
              >
                拒绝修改
              </button>
            </div>
          </section>
        ) : null}
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
                <button
                  type="button"
                  aria-label={`回退到版本 ${version.version_no}`}
                  disabled={
                    !onRestoreVersion ||
                    version.version_id === currentVersionId ||
                    revisionBusy ||
                    auditBusy ||
                    snapshotBlocked ||
                    Boolean(pendingPatch)
                  }
                  title={
                    version.version_id === currentVersionId
                      ? "当前版本"
                      : "复制此历史快照为新的当前版本"
                  }
                  onClick={() => onRestoreVersion?.(version.version_id)}
                >
                  回退到版本 {version.version_no}
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted-text">生成首个版本后显示历史记录。</p>
        )}
        <button
          type="button"
          className="secondary-button"
          disabled={
            !currentVersionId ||
            !onExportProject ||
            revisionBusy ||
            auditBusy ||
            snapshotBlocked
          }
          onClick={() => onExportProject?.()}
        >
          导出当前 Markdown
        </button>
      </section>
    </aside>
  );
}
