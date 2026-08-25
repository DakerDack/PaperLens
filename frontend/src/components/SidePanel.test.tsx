import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuditReport, EvidenceRecord, VersionSummary } from "../types";
import { SidePanel } from "./SidePanel";


const quickReport: AuditReport = {
  audit_status: "quick_complete",
  dimensions: [],
  risk_assessment: null,
  hard_failures: [],
  core_gate_passed: null,
  overall_score: null,
  decision: "pending_deep_audit",
};

const dimensionExpectations = [
  { dimension_id: "factual_consistency", label: "事实一致性" },
  { dimension_id: "citation_correctness", label: "引文正确性" },
  { dimension_id: "citation_completeness", label: "引文完整性" },
  { dimension_id: "method_scope", label: "方法与范围" },
  { dimension_id: "conclusion_limitations", label: "结论与局限" },
  { dimension_id: "terminology", label: "术语准确性" },
  { dimension_id: "reader_adaptation", label: "读者适配" },
  { dimension_id: "risk_compliance", label: "风险与合规" },
] as const;

const riskExpectations = [
  {
    category: "sensitive_information",
    label: "敏感信息",
    reason: "No sensitive-content risk was detected.",
    remediation: "No sensitive-content remediation is required.",
  },
  {
    category: "author_impersonation",
    label: "作者身份冒充",
    reason: "Synthetic author-impersonation check passed.",
    remediation: "No synthetic author-impersonation remediation is required.",
  },
  {
    category: "academic_integrity",
    label: "学术诚信",
    reason: "Synthetic academic-integrity check passed.",
    remediation: "No synthetic academic-integrity remediation is required.",
  },
] as const;

const deepReport: AuditReport = {
  audit_status: "deep_complete",
  dimensions: dimensionExpectations.map(({ dimension_id }) => ({
    dimension_id,
    raw_metrics: { level_points: 4 },
    score: 100,
    level: "good",
  })),
  risk_assessment: {
    compliance_context: {
      rights_or_license_confirmed: true,
      source_disclosure_status: "present",
      ai_assistance_disclosure_status: "present",
      generated_content_label_applicability: "not_applicable",
      generated_content_label_status: "not_applicable",
    },
    risk_findings: riskExpectations.map(({ category, reason, remediation }) => ({
      category,
      status: "not_detected",
      locations: [],
      reason,
      remediation,
    })),
    level_points: 4,
  },
  hard_failures: [],
  core_gate_passed: true,
  overall_score: 100,
  decision: "qualified",
};

const evidence: EvidenceRecord = {
  claim_id: "c-1",
  block_id: "p02-b001",
  page_index: 1,
  quote: "已从第二页核验的证据摘录。",
  bbox: null,
  match_method: "model_candidate",
  quote_verified: true,
  rule_flags: [],
};

const versions: VersionSummary[] = [
  {
    version_id: "v-1",
    version_no: 1,
    parent_version_id: null,
    reason: "initial_generation",
    created_at: "2026-08-25T08:00:00Z",
  },
];


afterEach(cleanup);


describe("SidePanel", () => {
  it("shows empty evidence and quick-check status without a final score", () => {
    render(
      <SidePanel
        selectedSentenceId={null}
        selectedEvidence={null}
        evidenceInsufficient
        quickReport={quickReport}
        deepReport={null}
        auditState="idle"
        auditError={null}
        versions={versions}
        canAudit
        onRunAudit={vi.fn()}
      />,
    );

    expect(screen.getByText("证据不足")).toBeTruthy();
    expect(screen.getByText("快速检查完成")).toBeTruthy();
    expect(screen.queryByText(/总分/)).toBeNull();
    expect(screen.queryByText("合格")).toBeNull();
    expect(screen.queryByRole("region", { name: "八维审计结果" })).toBeNull();
    expect(screen.queryByRole("region", { name: "结构化风险状态" })).toBeNull();
    expect(screen.getByRole("button", { name: "运行完整审计" })).toBeTruthy();
  });

  it("keeps the quick result and exposes retry after AUDIT_INCOMPLETE", () => {
    const onRunAudit = vi.fn();
    render(
      <SidePanel
        selectedSentenceId="s-1"
        selectedEvidence={null}
        evidenceInsufficient
        quickReport={quickReport}
        deepReport={null}
        auditState="failed"
        auditError={{
          code: "AUDIT_INCOMPLETE",
          message: "完整审计响应缺少所需风险类别，快速检查结果已经保留，请稍后重新尝试完整审计。",
          retryable: true,
        }}
        versions={versions}
        canAudit
        onRunAudit={onRunAudit}
      />,
    );

    expect(screen.getByText("完整审计失败")).toBeTruthy();
    expect(screen.getByText("快速检查结果已保留")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试完整审计" }));
    expect(onRunAudit).toHaveBeenCalledWith({
      source_disclosure_status: "present",
      ai_assistance_disclosure_status: "present",
      generated_content_label_applicability: "not_applicable",
      generated_content_label_status: "not_applicable",
    });
    expect(screen.queryByText(/总分/)).toBeNull();
    expect(screen.queryByText("合格")).toBeNull();
    expect(screen.queryByText("需要修改")).toBeNull();
    expect(screen.queryByText("不合格")).toBeNull();
    expect(screen.queryByRole("region", { name: "八维审计结果" })).toBeNull();
    expect(screen.queryByRole("region", { name: "结构化风险状态" })).toBeNull();
  });

  it("prioritizes the running gate over a supplied complete report", () => {
    render(
      <SidePanel
        selectedSentenceId="s-1"
        selectedEvidence={evidence}
        evidenceInsufficient={false}
        quickReport={quickReport}
        deepReport={deepReport}
        auditState="running"
        auditError={null}
        versions={versions}
        canAudit
        onRunAudit={vi.fn()}
      />,
    );

    expect(screen.getByText("深度审计中")).toBeTruthy();
    expect(screen.getByText("快速检查完成")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "八维审计结果" })).toBeNull();
    expect(screen.queryByRole("region", { name: "结构化风险状态" })).toBeNull();
    expect(screen.queryByText(/总分/)).toBeNull();
    expect(screen.queryByText("合格")).toBeNull();
    expect(screen.queryByText("需要修改")).toBeNull();
    expect(screen.queryByText("不合格")).toBeNull();
  });

  it("shows all structured final results only for a complete deep audit", () => {
    render(
      <SidePanel
        selectedSentenceId="s-1"
        selectedEvidence={evidence}
        evidenceInsufficient={false}
        quickReport={quickReport}
        deepReport={deepReport}
        auditState="complete"
        auditError={null}
        versions={versions}
        canAudit
        onRunAudit={vi.fn()}
      />,
    );

    expect(screen.getByText("第 2 页 · p02-b001")).toBeTruthy();
    expect(screen.getByText(evidence.quote as string)).toBeTruthy();
    const dimensionRegion = screen.getByRole("region", { name: "八维审计结果" });
    for (const { label } of dimensionExpectations) {
      expect(within(dimensionRegion).getAllByText(label)).toHaveLength(1);
    }
    expect(within(dimensionRegion).getAllByText("分数 100.0")).toHaveLength(8);
    expect(within(dimensionRegion).getAllByText("等级 good")).toHaveLength(8);

    const riskRegion = screen.getByRole("region", { name: "结构化风险状态" });
    for (const { label } of riskExpectations) {
      expect(within(riskRegion).getAllByText(label)).toHaveLength(1);
    }
    expect(within(riskRegion).getAllByText("not_detected")).toHaveLength(3);
    for (const finding of deepReport.risk_assessment?.risk_findings ?? []) {
      expect(screen.queryByText(finding.reason)).toBeNull();
      expect(screen.queryByText(finding.remediation)).toBeNull();
    }

    expect(screen.getByText("总分 100.0")).toBeTruthy();
    expect(screen.getByText("合格")).toBeTruthy();
    expect(screen.getByText("阶段 6 尚未开放")).toBeTruthy();
    expect(screen.getByText("版本 1")).toBeTruthy();
  });
});
