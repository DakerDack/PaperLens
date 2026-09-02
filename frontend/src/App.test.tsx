import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AuditReport,
  ContentDraft,
  EditPatch,
  ProjectView,
  VersionSummary,
} from "./types";
import { ApiClientError } from "./api";
import { App } from "./App";


const apiMocks = vi.hoisted(() => ({
  acceptRevision: vi.fn(),
  auditProject: vi.fn(),
  createProject: vi.fn(),
  createRevision: vi.fn(),
  exportProjectMarkdown: vi.fn(),
  generateProject: vi.fn(),
  getHealth: vi.fn(),
  getProject: vi.fn(),
  getProjectPdf: vi.fn(),
  rejectRevision: vi.fn(),
  restoreProjectVersion: vi.fn(),
}));


const RESTORE_KEY_1 = "123e4567-e89b-42d3-a456-426614174000";
const RESTORE_KEY_2 = "223e4567-e89b-42d3-a456-426614174000";


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
  pending_patch: null,
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

const sentencePatch: EditPatch = {
  patch_id: "patch-1",
  base_version: 1,
  scope: "sentence",
  target_sentence_ids: ["s-3"],
  before_hash: "a".repeat(64),
  before_text: "实验组表现更好。",
  after_text: "实验组在限定条件下表现更好。",
  reason: "补充限制条件。",
  fact_changed: false,
  evidence_changed: false,
};

const patchPendingProject: ProjectView = {
  ...quickProject,
  stage: "patch_pending",
  pending_patch: sentencePatch,
};

const revisedDocument: ContentDraft = {
  ...document,
  sections: document.sections.map((section) => ({
    ...section,
    sentences: section.sentences.map((sentence) =>
      sentence.sentence_id === "s-3"
        ? { ...sentence, text: sentencePatch.after_text }
        : sentence,
    ),
  })),
};

const revisedProject: ProjectView = {
  ...quickProject,
  current_version_id: "v-2",
  current_version_no: 2,
  document: revisedDocument,
  versions: [
    ...quickProject.versions,
    {
      version_id: "v-2",
      version_no: 2,
      parent_version_id: "v-1",
      reason: "accepted_patch:patch-1",
      created_at: "2026-08-25T08:05:00Z",
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
  apiMocks.rejectRevision.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});


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


function expectSnapshotControlsLocked() {
  for (const name of [
    "来源说明",
    "AI 辅助说明",
    "生成内容标识",
    "修改范围",
    "修改意图",
  ]) {
    expect(
      (
        screen.getByLabelText(name) as HTMLSelectElement | HTMLTextAreaElement
      ).disabled,
    ).toBe(true);
  }
  for (const name of ["运行完整审计", "生成补丁预览", "导出当前 Markdown"]) {
    expect(
      (screen.getByRole("button", { name }) as HTMLButtonElement).disabled,
    ).toBe(true);
  }
}


function expectSnapshotDependentControlsDisabled(
  safeActionName = "重试修改操作",
) {
  expectSnapshotControlsLocked();
  expect(
    (screen.getByRole("button", { name: safeActionName }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
}


function networkError(message = "无法确认服务端是否已提交写入。") {
  return new ApiClientError(0, {
    error_code: "NETWORK_ERROR",
    message,
    retryable: true,
    details: null,
  });
}


async function previewSentenceRevision() {
  apiMocks.createRevision.mockResolvedValue(sentencePatch);
  submitSentenceRevisionPreview();
  await screen.findByRole("region", { name: "修改前后差异" });
}


function submitSentenceRevisionPreview() {
  fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
  fireEvent.change(screen.getByLabelText("修改意图"), {
    target: { value: "补充限制条件。" },
  });
  fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
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

  it("restores the exact server pending patch and locks new snapshot mutations", async () => {
    await uploadProject(patchPendingProject);

    const diff = await screen.findByRole("region", { name: "修改前后差异" });
    expect(diff.textContent).toContain(sentencePatch.before_text);
    expect(diff.textContent).toContain(sentencePatch.after_text);
    expect(diff.textContent).toContain(sentencePatch.reason);
    expect(screen.getByRole("button", { name: "接受修改" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "拒绝修改" })).toBeTruthy();
    expect(apiMocks.createRevision).not.toHaveBeenCalled();
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();
    expect(apiMocks.rejectRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).not.toHaveBeenCalled();

    for (const name of ["修改范围", "修改意图"]) {
      expect(
        (screen.getByLabelText(name) as HTMLSelectElement | HTMLTextAreaElement)
          .disabled,
      ).toBe(true);
    }
    expect(
      (screen.getByRole("button", { name: "运行完整审计" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "生成补丁预览" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "导出当前 Markdown" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("records a successful preview as pending without changing the stable document", async () => {
    await reachQuickCheck();
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    await previewSentenceRevision();

    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /限定条件下表现更好/ })).toBeNull();
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview);
    expect(
      (screen.getByRole("button", { name: "运行完整审计" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "生成补丁预览" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "导出当前 Markdown" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("accepts a recovered server patch once and adopts only the refreshed snapshot", async () => {
    await uploadProject(patchPendingProject);
    await screen.findByRole("region", { name: "修改前后差异" });
    apiMocks.acceptRevision.mockResolvedValueOnce({
      project_id: "p-1",
      version_id: "v-2",
      stage: "quick_checked",
      quick_report: quickReport,
    });
    apiMocks.getProject.mockResolvedValueOnce(revisedProject);
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.acceptRevision).toHaveBeenCalledWith(
      "p-1",
      sentencePatch.patch_id,
      expect.any(AbortSignal),
    );
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
    expect(apiMocks.createRevision).not.toHaveBeenCalled();
    expect(apiMocks.rejectRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
  });

  it("rejects a recovered server patch once and refreshes the full stable snapshot", async () => {
    await uploadProject(patchPendingProject);
    await screen.findByRole("region", { name: "修改前后差异" });
    let finishRefresh!: (project: ProjectView) => void;
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectView>((resolve) => {
          finishRefresh = resolve;
        }),
    );
    const readsBeforeReject = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));

    await screen.findByText("正在刷新当前版本…");
    expect(
      screen.getByText("写操作已完成，正在读取服务端当前版本。"),
    ).toBeTruthy();
    expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.rejectRevision).toHaveBeenCalledWith(
      "p-1",
      sentencePatch.patch_id,
      expect.any(AbortSignal),
    );
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeReject + 1);
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    finishRefresh(quickProject);

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull(),
    );
    expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeReject + 1);
    expect(apiMocks.createRevision).not.toHaveBeenCalled();
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();
  });

  it("retries only the server read when rejection succeeded but refresh failed", async () => {
    await uploadProject(patchPendingProject);
    await screen.findByRole("region", { name: "修改前后差异" });
    apiMocks.getProject
      .mockRejectedValueOnce(networkError("拒绝后首次读取失败。"))
      .mockResolvedValueOnce(quickProject);
    const readsBeforeReject = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));

    await screen.findByText("当前版本刷新失败");
    expect(screen.queryByText("修改操作失败")).toBeNull();
    expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeReject + 1);
    expectSnapshotDependentControlsDisabled();
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));
    fireEvent.click(screen.getByRole("button", { name: "导出当前 Markdown" }));
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.createRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).not.toHaveBeenCalled();
    expect(apiMocks.exportProjectMarkdown).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "重试修改操作" }));

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull(),
    );
    expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeReject + 2);
  });

  it("clears a local patch when reconciliation returns a stable server view without it", async () => {
    await reachQuickCheck();
    await previewSentenceRevision();
    apiMocks.acceptRevision.mockRejectedValueOnce(networkError());
    apiMocks.getProject.mockResolvedValueOnce(quickProject);
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));
    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull(),
    );
    expect(screen.queryByText("写入结果尚未确认")).toBeNull();
    expect(screen.queryByText("修改操作失败")).toBeNull();
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
  });

  it("selects a sentence, exposes its excerpt, and sets the PDF target page", async () => {
    await reachQuickCheck();

    expect(apiMocks.generateProject).toHaveBeenCalledWith(
      "p-1",
      { claim_policy: "required" },
      expect.any(AbortSignal),
    );
    expect(screen.queryByRole("button", { name: /risk-only/i })).toBeNull();

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

  it("previews and rejects a sentence patch without overwriting the document", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    let finishReject: (() => void) | undefined;
    apiMocks.rejectRevision.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishReject = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });

    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("region", { name: "修改前后差异" });

    expect(apiMocks.createRevision).toHaveBeenCalledWith(
      "p-1",
      {
        base_version_id: "v-1",
        scope: "sentence",
        target_sentence_id: "s-3",
        user_instruction: "补充限制条件。",
      },
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));
    await waitFor(() =>
      expect(apiMocks.rejectRevision).toHaveBeenCalledWith(
        "p-1",
        "patch-1",
        expect.any(AbortSignal),
      ),
    );
    expect(screen.getByText("正在拒绝补丁…")).toBeTruthy();
    expect(screen.getByRole("region", { name: "修改前后差异" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    const auditButton = screen.getByRole("button", { name: "运行完整审计" });
    expect((auditButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(auditButton);
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();

    if (!finishReject) {
      throw new Error("reject request did not start");
    }
    apiMocks.getProject.mockResolvedValueOnce(quickProject);
    finishReject();
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull(),
    );
  });

  it("keeps a rejected patch preview visible after failure and retries the same patch", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    apiMocks.rejectRevision
      .mockRejectedValueOnce(
        new ApiClientError(0, {
          error_code: "NETWORK_ERROR",
          message: "无法连接本地服务。",
          retryable: true,
          details: null,
        }),
      )
      .mockResolvedValueOnce(undefined);
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("region", { name: "修改前后差异" });

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));

    await screen.findByText("NETWORK_ERROR");
    expect(screen.getByRole("region", { name: "修改前后差异" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();

    apiMocks.getProject.mockResolvedValueOnce(quickProject);
    fireEvent.click(screen.getByRole("button", { name: "重试修改操作" }));
    await waitFor(() => expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(2));
    expect(apiMocks.rejectRevision).toHaveBeenNthCalledWith(
      2,
      "p-1",
      "patch-1",
      expect.any(AbortSignal),
    );
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull(),
    );
  });

  it("prevents a deep audit from starting while a patch is pending", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("region", { name: "修改前后差异" });

    const auditButton = screen.getByRole("button", { name: "运行完整审计" });
    const acceptButton = screen.getByRole("button", { name: "接受修改" });
    const rejectButton = screen.getByRole("button", { name: "拒绝修改" });
    expect((auditButton as HTMLButtonElement).disabled).toBe(true);
    expect((acceptButton as HTMLButtonElement).disabled).toBe(false);
    expect((rejectButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(auditButton);
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();
    expect(apiMocks.rejectRevision).not.toHaveBeenCalled();
  });

  it("recovers the audit state when its request is cancelled", async () => {
    await reachQuickCheck();
    apiMocks.auditProject.mockRejectedValueOnce(
      new DOMException("request cancelled", "AbortError"),
    );

    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));

    await waitFor(() => expect(apiMocks.auditProject).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", {
          name: "运行完整审计",
        }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(screen.queryByText("深度审计中")).toBeNull();
    expect(screen.queryByText("完整审计失败")).toBeNull();
  });

  it("restores a cancelled rejection to the same patch preview", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    apiMocks.rejectRevision.mockRejectedValueOnce(
      new DOMException("request cancelled", "AbortError"),
    );
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("region", { name: "修改前后差异" });

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));

    await waitFor(() => expect(apiMocks.rejectRevision).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    expect(screen.getByRole("region", { name: "修改前后差异" })).toBeTruthy();
    expect(screen.queryByText("修改操作失败")).toBeNull();
  });

  it("accepts a preview only after confirmation and refreshes the server snapshot", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    apiMocks.acceptRevision.mockResolvedValue({
      project_id: "p-1",
      version_id: "v-2",
      stage: "quick_checked",
      quick_report: quickReport,
    });
    apiMocks.getProject.mockResolvedValueOnce(revisedProject);
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("button", { name: "接受修改" });

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.acceptRevision).toHaveBeenCalledWith(
      "p-1",
      "patch-1",
      expect.any(AbortSignal),
    );
    expect(apiMocks.getProject).toHaveBeenLastCalledWith(
      "p-1",
      expect.any(AbortSignal),
    );
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
  });

  it("retries only the project refresh after acceptance already succeeded", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockResolvedValue(sentencePatch);
    apiMocks.acceptRevision.mockResolvedValue({
      project_id: "p-1",
      version_id: "v-2",
      stage: "quick_checked",
      quick_report: quickReport,
    });
    let failInitialRefresh!: (reason?: unknown) => void;
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectView>((_resolve, reject) => {
          failInitialRefresh = reject;
        }),
    );
    let finishRefresh!: (project: ProjectView) => void;
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectView>((resolve) => {
          finishRefresh = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    await screen.findByRole("button", { name: "接受修改" });

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByText("正在刷新当前版本…");
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
    expect(
      screen.getByText("写操作已完成，正在读取服务端当前版本。"),
    ).toBeTruthy();

    failInitialRefresh(
      new ApiClientError(0, {
        error_code: "NETWORK_ERROR",
        message: "无法刷新当前项目。",
        retryable: true,
        details: null,
      }),
    );
    await screen.findByText("当前版本刷新失败");
    expect(screen.getByText("NETWORK_ERROR")).toBeTruthy();
    expect(
      screen.getByText("写操作已完成，但当前项目刷新失败。无法刷新当前项目。"),
    ).toBeTruthy();
    expectSnapshotDependentControlsDisabled();
    expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝修改" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    fireEvent.click(screen.getByRole("button", { name: "导出当前 Markdown" }));
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.exportProjectMarkdown).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重试修改操作" }));
    await screen.findByText("正在刷新当前版本…");
    expect(
      screen.getByText("写操作已完成，正在读取服务端当前版本。"),
    ).toBeTruthy();
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);

    finishRefresh(revisedProject);
    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(4);
  });

  it("restores a historical version through a fresh server read", async () => {
    await uploadProject(revisedProject);
    const randomUuid = vi
      .spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValue(RESTORE_KEY_1);
    const restoredProject: ProjectView = {
      ...quickProject,
      current_version_id: "v-3",
      current_version_no: 3,
      versions: [
        ...revisedProject.versions,
        {
          version_id: "v-3",
          version_no: 3,
          parent_version_id: "v-2",
          reason: "restore:v-1",
          created_at: "2026-08-25T08:10:00Z",
        },
      ],
    };
    const restoreSummary: VersionSummary = restoredProject.versions.at(-1)!;
    apiMocks.restoreProjectVersion.mockResolvedValue(restoreSummary);
    apiMocks.getProject.mockResolvedValueOnce(restoredProject);
    await screen.findByRole("button", { name: "回退到版本 1" });

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

    await screen.findByRole("button", { name: /实验组表现更好/ });
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledWith(
      "p-1",
      "v-1",
      RESTORE_KEY_1,
      expect.any(AbortSignal),
    );
    expect(randomUuid).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenLastCalledWith(
      "p-1",
      expect.any(AbortSignal),
    );
    expect(screen.getAllByText("版本 3").length).toBeGreaterThanOrEqual(2);
  });

  it("retries only the project refresh after restore already succeeded", async () => {
    await uploadProject(revisedProject);
    const restoredProject: ProjectView = {
      ...quickProject,
      current_version_id: "v-3",
      current_version_no: 3,
      versions: [
        ...revisedProject.versions,
        {
          version_id: "v-3",
          version_no: 3,
          parent_version_id: "v-2",
          reason: "restore:v-1",
          created_at: "2026-08-25T08:10:00Z",
        },
      ],
    };
    apiMocks.restoreProjectVersion.mockResolvedValue(restoredProject);
    let failInitialRefresh!: (reason?: unknown) => void;
    apiMocks.getProject
      .mockImplementationOnce(
        () =>
          new Promise<ProjectView>((_resolve, reject) => {
            failInitialRefresh = reject;
          }),
      )
      .mockResolvedValueOnce(restoredProject);
    await screen.findByRole("button", { name: "回退到版本 1" });

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));
    await screen.findByText("正在刷新当前版本…");
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText("写操作已完成，正在读取服务端当前版本。"),
    ).toBeTruthy();

    failInitialRefresh(
      new ApiClientError(0, {
        error_code: "NETWORK_ERROR",
        message: "无法刷新当前项目。",
        retryable: true,
        details: null,
      }),
    );
    await screen.findByText("当前版本刷新失败");
    expect(
      screen.getByText("写操作已完成，但当前项目刷新失败。无法刷新当前项目。"),
    ).toBeTruthy();
    expectSnapshotDependentControlsDisabled();
    const restoreButton = screen.getByRole("button", { name: "回退到版本 1" });
    expect((restoreButton as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝修改" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    fireEvent.click(restoreButton);
    fireEvent.click(screen.getByRole("button", { name: "导出当前 Markdown" }));
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.createRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.exportProjectMarkdown).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重试修改操作" }));

    await screen.findByRole("button", { name: /实验组表现更好/ });
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(3);
    expect(screen.getAllByText("版本 3").length).toBeGreaterThanOrEqual(2);
  });

  it("reconciles an uncertain accept through one server read without replaying it", async () => {
    await reachQuickCheck();
    await previewSentenceRevision();
    apiMocks.acceptRevision.mockRejectedValueOnce(networkError());
    let finishReconciliation!: (project: ProjectView) => void;
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectView>((resolve) => {
          finishReconciliation = resolve;
        }),
    );
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByText("写入结果尚未确认");
    expect(
      screen.getByText("写入结果尚未确认，需要读取服务端当前版本。"),
    ).toBeTruthy();
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expect(screen.queryByText("修改操作失败")).toBeNull();
    expect(screen.getByRole("region", { name: "修改前后差异" })).toBeTruthy();
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept);

    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await screen.findByText("正在读取服务端当前版本…");
    expectSnapshotControlsLocked();
    expect(screen.queryByRole("button", { name: "读取服务端当前版本" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);

    finishReconciliation(revisedProject);
    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
  });

  it("keeps the pending patch only when accept reconciliation still reports it", async () => {
    await reachQuickCheck();
    await previewSentenceRevision();
    apiMocks.acceptRevision.mockRejectedValueOnce(networkError());
    apiMocks.getProject.mockResolvedValueOnce(patchPendingProject);
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));
    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await screen.findByRole("region", { name: "修改前后差异" });
    expect(screen.getByRole("button", { name: /实验组表现更好/ })).toBeTruthy();
    expect(screen.getAllByText("版本 1").length).toBeGreaterThanOrEqual(2);
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
  });

  it("retries only reconciliation reads after an uncertain restore and repeated read failures", async () => {
    await uploadProject(revisedProject);
    await screen.findByRole("button", { name: "回退到版本 1" });
    apiMocks.restoreProjectVersion.mockRejectedValueOnce(networkError());
    apiMocks.getProject
      .mockRejectedValueOnce(networkError("第一次对账读取失败。"))
      .mockRejectedValueOnce(networkError("第二次对账读取失败。"));
    const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

    await screen.findByText("写入结果尚未确认");
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expect(screen.queryByText("修改操作失败")).toBeNull();
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
    await waitFor(() =>
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 1),
    );
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", { name: "读取服务端当前版本" }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
    await waitFor(() =>
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 2),
    );
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", { name: "读取服务端当前版本" }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    expect(screen.getByText("写入结果尚未确认")).toBeTruthy();
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expect(screen.queryByText("修改操作失败")).toBeNull();
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
  });

  it("returns to an explicit restore choice when reconciliation still shows the old version", async () => {
    const randomUuid = vi
      .spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce(RESTORE_KEY_1)
      .mockReturnValueOnce(RESTORE_KEY_2);
    await uploadProject(revisedProject);
    await screen.findByRole("button", { name: "回退到版本 1" });
    apiMocks.restoreProjectVersion
      .mockRejectedValueOnce(networkError())
      .mockRejectedValueOnce(
        new ApiClientError(404, {
          error_code: "VERSION_NOT_FOUND",
          message: "历史版本不存在。",
          retryable: false,
          details: null,
        }),
      );
    apiMocks.getProject.mockResolvedValueOnce(revisedProject);
    const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));
    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 1);

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

    await screen.findByText("VERSION_NOT_FOUND");
    expect(randomUuid).toHaveBeenCalledTimes(2);
    expect(apiMocks.restoreProjectVersion).toHaveBeenNthCalledWith(
      1,
      "p-1",
      "v-1",
      RESTORE_KEY_1,
      expect.any(AbortSignal),
    );
    expect(apiMocks.restoreProjectVersion).toHaveBeenNthCalledWith(
      2,
      "p-1",
      "v-1",
      RESTORE_KEY_2,
      expect.any(AbortSignal),
    );
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 1);
  });

  it.each([
    [503, "SERVICE_UNAVAILABLE", "服务暂时不可用。"],
    [409, "UNKNOWN_CONFLICT", "未知冲突。"],
  ])(
    "treats accept %s/%s as uncertain and reconciles without replaying accept",
    async (status, errorCode, message) => {
      await reachQuickCheck();
      await previewSentenceRevision();
      apiMocks.acceptRevision.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message,
          retryable: true,
          details: null,
        }),
      );
      apiMocks.getProject.mockResolvedValueOnce(revisedProject);
      const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

      await screen.findByText("写入结果尚未确认");
      expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept);
      fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

      await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
      expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
    },
  );

  it("treats a restore 500 as uncertain and reconciles without replaying restore", async () => {
    await uploadProject(revisedProject);
    await screen.findByRole("button", { name: "回退到版本 1" });
    apiMocks.restoreProjectVersion.mockRejectedValueOnce(
      new ApiClientError(500, {
        error_code: "INTERNAL_ERROR",
        message: "服务内部错误。",
        retryable: true,
        details: null,
      }),
    );
    apiMocks.getProject.mockResolvedValueOnce(revisedProject);
    const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

    await screen.findByText("写入结果尚未确认");
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore);
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 1);
  });

  it("treats accept 404/VERSION_NOT_FOUND as uncertain and reconciles without replaying accept", async () => {
    await reachQuickCheck();
    await previewSentenceRevision();
    apiMocks.acceptRevision.mockRejectedValueOnce(
      new ApiClientError(404, {
        error_code: "VERSION_NOT_FOUND",
        message: "历史版本不存在。",
        retryable: false,
        details: null,
      }),
    );
    apiMocks.getProject.mockResolvedValueOnce(revisedProject);
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByText("写入结果尚未确认");
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept);
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept + 1);
  });

  it.each([
    [409, "TARGET_STALE", "当前版本已经变化。"],
    [422, "PATCH_INVALID", "补丁已经失效。"],
  ])(
    "treats restore %s/%s as uncertain and reconciles without replaying restore",
    async (status, errorCode, message) => {
      await uploadProject(revisedProject);
      await screen.findByRole("button", { name: "回退到版本 1" });
      apiMocks.restoreProjectVersion.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message,
          retryable: false,
          details: null,
        }),
      );
      apiMocks.getProject.mockResolvedValueOnce(revisedProject);
      const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

      await screen.findByText("写入结果尚未确认");
      expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore);
      fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

      await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
      expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore + 1);
    },
  );

  it.each([
    ["AbortError", new DOMException("accept cancelled", "AbortError")],
    [
      "parsed success response failure",
      new ApiClientError(200, {
        error_code: "INVALID_RESPONSE",
        message: "服务返回了无法读取的数据，请重试。",
        retryable: true,
        details: null,
      }),
    ],
  ])("keeps an uncertain accept locked after %s", async (_label, error) => {
    await reachQuickCheck();
    await previewSentenceRevision();
    apiMocks.acceptRevision.mockRejectedValueOnce(error);
    const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

    await screen.findByText("写入结果尚未确认");
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept);
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it.each([
    ["AbortError", new DOMException("restore cancelled", "AbortError")],
    [
      "parsed success response failure",
      new ApiClientError(200, {
        error_code: "INVALID_RESPONSE",
        message: "服务返回了无法读取的数据，请重试。",
        retryable: true,
        details: null,
      }),
    ],
  ])("keeps an uncertain restore locked after %s", async (_label, error) => {
    await uploadProject(revisedProject);
    await screen.findByRole("button", { name: "回退到版本 1" });
    apiMocks.restoreProjectVersion.mockRejectedValueOnce(error);
    const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

    await screen.findByText("写入结果尚未确认");
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore);
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it.each([
    [409, "TARGET_STALE", "当前版本已经变化。"],
    [422, "PATCH_INVALID", "补丁已经失效。"],
  ])(
    "keeps explicit accept error %s/%s without entering reconciliation",
    async (status, errorCode, message) => {
      await reachQuickCheck();
      await previewSentenceRevision();
      apiMocks.acceptRevision.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message,
          retryable: false,
          details: null,
        }),
      );
      const readsBeforeAccept = apiMocks.getProject.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "接受修改" }));

      await screen.findByText(errorCode);
      expect(screen.getByText(message)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "读取服务端当前版本" })).toBeNull();
      expect(screen.queryByRole("button", { name: "重试修改操作" })).toBeNull();
      expect(apiMocks.acceptRevision).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeAccept);
    },
  );

  it.each([
    [404, "VERSION_NOT_FOUND", "历史版本不存在。"],
    [422, "IDEMPOTENCY_KEY_INVALID", "幂等键格式无效。"],
    [409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他回退目标。"],
  ])(
    "keeps explicit restore error %s/%s without entering reconciliation",
    async (status, errorCode, message) => {
      await uploadProject(revisedProject);
      await screen.findByRole("button", { name: "回退到版本 1" });
      apiMocks.restoreProjectVersion.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message,
          retryable: false,
          details: null,
        }),
      );
      const readsBeforeRestore = apiMocks.getProject.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "回退到版本 1" }));

      await screen.findByText(errorCode);
      expect(screen.getByText(message)).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: "读取服务端当前版本" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "重试修改操作" })).toBeNull();
      expect(apiMocks.restoreProjectVersion).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforeRestore);
    },
  );

  it("locks a NETWORK_ERROR preview result until a server read reconciles it", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(networkError());
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    submitSentenceRevisionPreview();

    await screen.findByText("写入结果尚未确认");
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview);
    fireEvent.click(screen.getByRole("button", { name: "运行完整审计" }));
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    fireEvent.click(screen.getByRole("button", { name: "导出当前 Markdown" }));
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.exportProjectMarkdown).not.toHaveBeenCalled();
  });

  it("keeps an aborted preview locked instead of reopening mutation replay", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(
      new DOMException("preview cancelled", "AbortError"),
    );

    submitSentenceRevisionPreview();

    await screen.findByText("写入结果尚未确认");
    expectSnapshotDependentControlsDisabled("读取服务端当前版本");
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
  });

  it("reconciles an unreadable successful preview response only through getProject", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(
      new ApiClientError(200, {
        error_code: "INVALID_RESPONSE",
        message: "服务返回了无法读取的数据，请重试。",
        retryable: true,
        details: null,
      }),
    );
    apiMocks.getProject.mockResolvedValueOnce(quickProject);
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    submitSentenceRevisionPreview();

    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
    await waitFor(() =>
      expect(screen.queryByText("写入结果尚未确认")).toBeNull(),
    );
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1);
  });

  it.each([
    [500, "HY3_UNAVAILABLE"],
    [409, "PATCH_INVALID"],
  ])(
    "treats preview %s/%s as uncertain and never retries the mutation",
    async (status, errorCode) => {
      await reachQuickCheck();
      apiMocks.createRevision.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message: "无法确定补丁是否已保存。",
          retryable: true,
          details: null,
        }),
      );
      apiMocks.getProject.mockResolvedValueOnce(quickProject);
      const readsBeforePreview = apiMocks.getProject.mock.calls.length;

      submitSentenceRevisionPreview();

      await screen.findByText("写入结果尚未确认");
      fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
      await waitFor(() =>
        expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1),
      );
      expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    },
  );

  it("retries only getProject after two failed preview reconciliations", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(networkError());
    apiMocks.getProject
      .mockRejectedValueOnce(networkError("首次对账失败。"))
      .mockRejectedValueOnce(networkError("再次对账失败。"));
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    submitSentenceRevisionPreview();
    await screen.findByText("写入结果尚未确认");

    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
    await waitFor(() =>
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1),
    );
    expect(
      screen.getByText(
        "写入结果仍未确认，读取服务端当前版本未完成。请再次读取。",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));
    await waitFor(() =>
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 2),
    );
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.acceptRevision).not.toHaveBeenCalled();
    expect(apiMocks.rejectRevision).not.toHaveBeenCalled();
    expect(apiMocks.restoreProjectVersion).not.toHaveBeenCalled();
    expect(apiMocks.auditProject).not.toHaveBeenCalled();
  });

  it("restores the complete server pending patch after uncertain preview reconciliation", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(networkError());
    apiMocks.getProject.mockResolvedValueOnce(patchPendingProject);
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    submitSentenceRevisionPreview();
    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    const diff = await screen.findByRole("region", { name: "修改前后差异" });
    expect(diff.textContent).toContain(sentencePatch.before_text);
    expect(diff.textContent).toContain(sentencePatch.after_text);
    expect(diff.textContent).toContain(sentencePatch.reason);
    expect(
      (screen.getByRole("button", { name: "接受修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(
      (screen.getByRole("button", { name: "拒绝修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1);
  });

  it("clears stale local preview state when reconciliation confirms no pending patch", async () => {
    await reachQuickCheck();
    apiMocks.createRevision
      .mockRejectedValueOnce(networkError())
      .mockResolvedValueOnce(sentencePatch);
    apiMocks.getProject.mockResolvedValueOnce(quickProject);
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;

    submitSentenceRevisionPreview();
    await screen.findByText("写入结果尚未确认");
    fireEvent.click(screen.getByRole("button", { name: "读取服务端当前版本" }));

    await waitFor(() =>
      expect(screen.queryByText("写入结果尚未确认")).toBeNull(),
    );
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "生成补丁预览" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1);

    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));

    await screen.findByRole("region", { name: "修改前后差异" });
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(2);
  });

  it.each([
    [404, "PROJECT_NOT_FOUND"],
    [409, "PROJECT_NOT_READY"],
    [422, "PATCH_INVALID"],
  ])(
    "keeps exact preview no-commit rejection %s/%s out of reconciliation",
    async (status, errorCode) => {
      await reachQuickCheck();
      apiMocks.createRevision.mockRejectedValueOnce(
        new ApiClientError(status, {
          error_code: errorCode,
          message: "服务端已明确拒绝补丁请求。",
          retryable: false,
          details: null,
        }),
      );
      const readsBeforePreview = apiMocks.getProject.mock.calls.length;

      submitSentenceRevisionPreview();

      await screen.findByText(errorCode);
      expect(screen.getByText("服务端已明确拒绝补丁请求。")).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: "读取服务端当前版本" }),
      ).toBeNull();
      expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
      expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview);
    },
  );

  it("refreshes and locks a stale preview before a new preview uses the latest base version", async () => {
    await reachQuickCheck();
    const stale = new ApiClientError(409, {
      error_code: "TARGET_STALE",
      message: "当前版本已经变化，请刷新后重试。",
      retryable: false,
      details: null,
    });
    apiMocks.createRevision
      .mockRejectedValueOnce(stale)
      .mockResolvedValueOnce(sentencePatch);
    let finishStaleRefresh!: (project: ProjectView) => void;
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectView>((resolve) => {
          finishStaleRefresh = resolve;
        }),
    );
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));

    await screen.findByText("正在刷新当前版本…");
    expect(
      screen.getByText("当前修订目标已过期，正在读取服务端当前版本。"),
    ).toBeTruthy();
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expectSnapshotControlsLocked();
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝修改" })).toBeNull();
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
    expect(apiMocks.createRevision).toHaveBeenNthCalledWith(
      1,
      "p-1",
      {
        base_version_id: "v-1",
        scope: "sentence",
        target_sentence_id: "s-3",
        user_instruction: "补充限制条件。",
      },
      expect.any(AbortSignal),
    );
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1);

    finishStaleRefresh(revisedProject);

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("TARGET_STALE")).toBeNull();
    expect(screen.queryByRole("region", { name: "修改前后差异" })).toBeNull();
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);

    fireEvent.click(
      screen.getByRole("button", { name: /实验组在限定条件下表现更好/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));

    await screen.findByRole("region", { name: "修改前后差异" });
    expect(apiMocks.createRevision).toHaveBeenNthCalledWith(
      2,
      "p-1",
      {
        base_version_id: "v-2",
        scope: "sentence",
        target_sentence_id: "s-3",
        user_instruction: "补充限制条件。",
      },
      expect.any(AbortSignal),
    );
  });

  it("retries only getProject after a stale preview refresh fails", async () => {
    await reachQuickCheck();
    apiMocks.createRevision.mockRejectedValueOnce(
      new ApiClientError(409, {
        error_code: "TARGET_STALE",
        message: "当前版本已经变化，请刷新后重试。",
        retryable: false,
        details: null,
      }),
    );
    apiMocks.getProject
      .mockRejectedValueOnce(networkError("读取最新项目失败。"))
      .mockResolvedValueOnce(revisedProject);
    const readsBeforePreview = apiMocks.getProject.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    fireEvent.change(screen.getByLabelText("修改意图"), {
      target: { value: "补充限制条件。" },
    });

    fireEvent.click(screen.getByRole("button", { name: "生成补丁预览" }));

    await screen.findByText("当前版本刷新失败");
    expect(screen.getByText("NETWORK_ERROR")).toBeTruthy();
    expect(screen.queryByText(/写操作已完成/)).toBeNull();
    expectSnapshotDependentControlsDisabled();
    expect(
      (screen.getByRole("button", { name: "回退到版本 1" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 1);

    fireEvent.click(screen.getByRole("button", { name: "重试修改操作" }));

    await screen.findByRole("button", { name: /实验组在限定条件下表现更好/ });
    expect(apiMocks.createRevision).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProject).toHaveBeenCalledTimes(readsBeforePreview + 2);
    expect(screen.getAllByText("版本 2").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("NETWORK_ERROR")).toBeNull();
  });

  it("exports the current stable version through the stage-six client", async () => {
    await uploadProject(quickProject);
    apiMocks.exportProjectMarkdown.mockResolvedValue(
      new Blob(["# 面向本科生的论文解读"], { type: "text/markdown" }),
    );
    await screen.findByRole("button", { name: "导出当前 Markdown" });

    fireEvent.click(screen.getByRole("button", { name: "导出当前 Markdown" }));

    await waitFor(() =>
      expect(apiMocks.exportProjectMarkdown).toHaveBeenCalledWith(
        "p-1",
        expect.any(AbortSignal),
      ),
    );
  });
});
