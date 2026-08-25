import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditReport, ContentDraft, ProjectView } from "./types";
import { ApiClientError } from "./api";
import { App } from "./App";


const apiMocks = vi.hoisted(() => ({
  auditProject: vi.fn(),
  createProject: vi.fn(),
  generateProject: vi.fn(),
  getHealth: vi.fn(),
  getProject: vi.fn(),
  getProjectPdf: vi.fn(),
}));


vi.mock("./api", () => {
  class MockApiClientError extends Error {
    readonly status: number;
    readonly errorCode: string;
    readonly retryable: boolean;
    readonly details: Record<string, unknown> | null;

    constructor(
      status: number,
      payload: {
        error_code: string;
        message: string;
        retryable: boolean;
        details: Record<string, unknown> | null;
      },
    ) {
      super(payload.message);
      this.status = status;
      this.errorCode = payload.error_code;
      this.retryable = payload.retryable;
      this.details = payload.details;
    }
  }

  return {
    ...apiMocks,
    ApiClientError: MockApiClientError,
    createRequestHandle: () => {
      const controller = new AbortController();
      return { signal: controller.signal, cancel: () => controller.abort() };
    },
    isRequestCancelled: (error: unknown) =>
      error instanceof DOMException && error.name === "AbortError",
  };
});

vi.mock("./components/PdfPane", () => ({
  PdfPane: ({ target }: { target: { pageIndex: number; quote: string } | null }) => (
    <section aria-label="PDF 阅读器" data-testid="pdf-target">
      {target ? `page=${target.pageIndex + 1};quote=${target.quote}` : "no-target"}
    </section>
  ),
}));

vi.mock("lucide-react", () => ({
  CheckCircle2: () => null,
  FileUp: () => null,
  LoaderCircle: () => null,
  Play: () => null,
  RefreshCw: () => null,
}));


const document: ContentDraft = {
  title: "面向本科生的论文解读",
  sections: [
    { section_id: "research_question", heading: "研究问题", sentences: [{ sentence_id: "s-1", text: "研究关注学习效果。" }] },
    { section_id: "methods", heading: "研究方法", sentences: [{ sentence_id: "s-2", text: "研究采用对照实验。" }] },
    { section_id: "results", heading: "主要结果", sentences: [{ sentence_id: "s-3", text: "实验组表现更好。" }] },
    { section_id: "limitations", heading: "研究局限", sentences: [{ sentence_id: "s-4", text: "样本规模较小。" }] },
    { section_id: "plain_explanation", heading: "通俗解释", sentences: [{ sentence_id: "s-5", text: "可以把它理解为一次课堂比较。" }] },
  ],
};

const quickReport: AuditReport = {
  audit_status: "quick_complete",
  dimensions: [],
  risk_assessment: null,
  hard_failures: [],
  core_gate_passed: null,
  overall_score: null,
  decision: "pending_deep_audit",
};

const baseProject: ProjectView = {
  project_id: "p-1",
  stage: "parsed",
  model_mode: "mock",
  parse_quality: {
    page_count: 2,
    block_count: 4,
    empty_page_rate: 0,
    abnormal_character_rate: 0,
    page_number_completeness_rate: 1,
    bbox_availability_rate: 0,
  },
  source_block_count: 4,
  current_version_id: null,
  current_version_no: null,
  document: null,
  claims: [],
  evidence_records: [],
  audit_report: null,
  versions: [],
  error_code: null,
  retryable_stage: null,
  created_at: "2026-08-25T08:00:00Z",
  updated_at: "2026-08-25T08:00:00Z",
};

const quickProject: ProjectView = {
  ...baseProject,
  stage: "quick_checked",
  current_version_id: "v-1",
  current_version_no: 1,
  document,
  claims: [
    {
      claim_id: "c-3",
      sentence_id: "s-3",
      text: "实验组表现更好。",
      claim_type: "result",
      importance: "critical",
      qualifiers: [],
      numeric_entities: [],
      auditability: "auditable",
      candidate_block_ids: ["p02-b001"],
      candidate_quote: "第二页证据摘录",
    },
  ],
  evidence_records: [
    {
      claim_id: "c-3",
      block_id: "p02-b001",
      page_index: 1,
      quote: "第二页证据摘录",
      bbox: null,
      match_method: "model_candidate",
      quote_verified: true,
      rule_flags: [],
    },
  ],
  audit_report: quickReport,
  versions: [
    {
      version_id: "v-1",
      version_no: 1,
      parent_version_id: null,
      reason: "initial_generation",
      created_at: "2026-08-25T08:01:00Z",
    },
  ],
};


beforeEach(() => {
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
  apiMocks.getHealth.mockResolvedValue({
    status: "ok",
    service: "paperlens-api",
    version: "0.1.0",
  });
  apiMocks.createProject.mockResolvedValue({ project_id: "p-1", stage: "parsed" });
  apiMocks.getProjectPdf.mockResolvedValue(new Blob(["%PDF-test"]));
  apiMocks.generateProject.mockResolvedValue({
    project_id: "p-1",
    stage: "quick_checked",
  });
});

afterEach(cleanup);


async function reachQuickCheck() {
  apiMocks.getProject.mockResolvedValueOnce(baseProject).mockResolvedValueOnce(quickProject);
  render(<App />);
  const file = new File(["%PDF-test"], "paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("选择 PDF"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("checkbox", { name: /确认拥有处理权限/ }));
  fireEvent.click(screen.getByRole("button", { name: "上传论文 PDF" }));
  await screen.findByRole("button", { name: "生成五区解读" });
  fireEvent.click(screen.getByRole("button", { name: "生成五区解读" }));
  await screen.findByText("实验组表现更好。");
}

async function uploadProject(project: ProjectView) {
  apiMocks.getProject.mockResolvedValueOnce(project);
  render(<App />);
  const file = new File(["%PDF-test"], "paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("选择 PDF"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("checkbox", { name: /确认拥有处理权限/ }));
  fireEvent.click(screen.getByRole("button", { name: "上传论文 PDF" }));
}


describe("PaperLens workbench", () => {
  it("starts with an upload action and all three work areas", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: "上传论文 PDF" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "PDF" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "解读" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "证据审计" })).toBeTruthy();
    expect(screen.getByText("模式待确认")).toBeTruthy();
    expect(screen.queryByText("Mock")).toBeNull();
    expect(screen.queryByText("Live")).toBeNull();
  });

  it("shows Mock only after the backend returns a mock project", async () => {
    await uploadProject(baseProject);

    expect(await screen.findByText("Mock")).toBeTruthy();
    expect(screen.queryByText("Live")).toBeNull();
    expect(screen.queryByText("模式待确认")).toBeNull();
  });

  it("shows Live only after the backend returns a live project", async () => {
    await uploadProject({ ...baseProject, model_mode: "live" });

    expect(await screen.findByText("Live")).toBeTruthy();
    expect(screen.queryByText("Mock")).toBeNull();
    expect(screen.queryByText("模式待确认")).toBeNull();
  });

  it("selects a sentence, exposes its excerpt, and sets the PDF target page", async () => {
    await reachQuickCheck();

    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));

    expect(screen.getByText("第二页证据摘录")).toBeTruthy();
    expect(screen.getByTestId("pdf-target").textContent).toContain("page=2");
    expect(screen.getAllByText("快速检查完成").length).toBeGreaterThan(0);
    expect(screen.queryByText(/总分/)).toBeNull();
  });

  it("keeps quick results visible and retries an AUDIT_INCOMPLETE error", async () => {
    await reachQuickCheck();
    const auditError = new ApiClientError(502, {
      error_code: "AUDIT_INCOMPLETE",
      message: "完整审计响应不完整，请稍后重试。",
      retryable: true,
      details: { boundary: "HY3_RISK_CATEGORY_COVERAGE_INVALID" },
    });
    apiMocks.auditProject.mockRejectedValue(auditError);

    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));
    await screen.findByText("完整审计失败");
    expect(screen.getByText("快速检查结果已保留")).toBeTruthy();
    expect(screen.queryByText(/总分/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试完整审计" }));
    await waitFor(() => expect(apiMocks.auditProject).toHaveBeenCalledTimes(2));
  });
});
