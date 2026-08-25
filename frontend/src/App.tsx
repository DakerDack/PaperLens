import { CheckCircle2, FileUp, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  auditProject,
  createProject,
  createRequestHandle,
  generateProject,
  getHealth,
  getProject,
  getProjectPdf,
  isRequestCancelled,
  type RequestHandle,
} from "./api";
import { DocumentPane } from "./components/DocumentPane";
import { PdfPane, type PdfTarget } from "./components/PdfPane";
import {
  SidePanel,
  type AuditUiError,
  type AuditUiState,
} from "./components/SidePanel";
import type {
  AuditReport,
  DeepAuditRequest,
  EvidenceRecord,
  ProjectView,
} from "./types";


type ProjectOperation = "idle" | "parsing" | "generating";
type RetryAction = "upload" | "generate";
type MobilePanel = "pdf" | "document" | "evidence";

interface WorkflowError extends AuditUiError {
  action: RetryAction;
}


function toUiError(error: unknown): Omit<WorkflowError, "action"> {
  if (error instanceof ApiClientError) {
    return {
      code: error.errorCode,
      message: error.message,
      retryable: error.retryable,
    };
  }
  return {
    code: "UNKNOWN_ERROR",
    message: "操作未完成，请检查本地服务后重试。",
    retryable: true,
  };
}


export function App() {
  const requestRef = useRef<RequestHandle | null>(null);
  const pdfUrlRef = useRef<string | null>(null);
  const [serviceState, setServiceState] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const [serviceVersion, setServiceVersion] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [project, setProject] = useState<ProjectView | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [operation, setOperation] = useState<ProjectOperation>("idle");
  const [workflowError, setWorkflowError] = useState<WorkflowError | null>(null);
  const [auditState, setAuditState] = useState<AuditUiState>("idle");
  const [auditError, setAuditError] = useState<AuditUiError | null>(null);
  const [lastQuickReport, setLastQuickReport] = useState<AuditReport | null>(null);
  const [selectedSentenceId, setSelectedSentenceId] = useState<string | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceRecord | null>(null);
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null);
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>("document");

  useEffect(() => {
    const request = createRequestHandle();
    getHealth(request.signal)
      .then((health) => {
        setServiceState("online");
        setServiceVersion(health.version);
      })
      .catch((error: unknown) => {
        if (!isRequestCancelled(error)) {
          setServiceState("offline");
        }
      });
    return () => request.cancel();
  }, []);

  useEffect(
    () => () => {
      requestRef.current?.cancel();
      if (pdfUrlRef.current) {
        URL.revokeObjectURL(pdfUrlRef.current);
      }
    },
    [],
  );

  const beginRequest = useCallback(() => {
    requestRef.current?.cancel();
    const request = createRequestHandle();
    requestRef.current = request;
    return request;
  }, []);

  const finishRequest = useCallback((request: RequestHandle) => {
    if (requestRef.current === request) {
      requestRef.current = null;
    }
  }, []);

  const replacePdfUrl = useCallback((blob: Blob) => {
    const nextUrl = URL.createObjectURL(blob);
    if (pdfUrlRef.current) {
      URL.revokeObjectURL(pdfUrlRef.current);
    }
    pdfUrlRef.current = nextUrl;
    setPdfUrl(nextUrl);
  }, []);

  const runUpload = useCallback(async () => {
    if (!file || !rightsConfirmed) {
      return;
    }
    const request = beginRequest();
    setOperation("parsing");
    setWorkflowError(null);
    setAuditError(null);
    setAuditState("idle");
    setSelectedSentenceId(null);
    setSelectedEvidence(null);
    setPdfTarget(null);
    setLastQuickReport(null);

    try {
      const created = await createProject(file, request.signal);
      const [nextProject, pdfBlob] = await Promise.all([
        getProject(created.project_id, request.signal),
        getProjectPdf(created.project_id, request.signal),
      ]);
      setProject(nextProject);
      replacePdfUrl(pdfBlob);
      setOperation("idle");
    } catch (error: unknown) {
      if (isRequestCancelled(error)) {
        return;
      }
      setOperation("idle");
      setWorkflowError({ ...toUiError(error), action: "upload" });
    } finally {
      finishRequest(request);
    }
  }, [beginRequest, file, finishRequest, replacePdfUrl, rightsConfirmed]);

  const runGeneration = useCallback(async () => {
    if (!project) {
      return;
    }
    const request = beginRequest();
    setOperation("generating");
    setWorkflowError(null);
    setAuditError(null);
    setAuditState("idle");

    try {
      const generated = await generateProject(project.project_id, request.signal);
      setLastQuickReport(generated.quick_report ?? null);
      const nextProject = await getProject(project.project_id, request.signal);
      setProject(nextProject);
      setOperation("idle");
      setMobilePanel("document");
    } catch (error: unknown) {
      if (isRequestCancelled(error)) {
        return;
      }
      setOperation("idle");
      setWorkflowError({ ...toUiError(error), action: "generate" });
    } finally {
      finishRequest(request);
    }
  }, [beginRequest, finishRequest, project]);

  const runAudit = useCallback(
    async (input: DeepAuditRequest) => {
      if (!project) {
        return;
      }
      const request = beginRequest();
      setAuditState("running");
      setAuditError(null);
      setWorkflowError(null);

      try {
        const audited = await auditProject(project.project_id, input, request.signal);
        const nextProject = await getProject(project.project_id, request.signal);
        setProject(nextProject);
        setAuditState("complete");
        if (audited.audit_report.audit_status === "deep_complete") {
          setMobilePanel("evidence");
        }
      } catch (error: unknown) {
        if (isRequestCancelled(error)) {
          return;
        }
        setAuditError(toUiError(error));
        setAuditState("failed");
      } finally {
        finishRequest(request);
      }
    },
    [beginRequest, finishRequest, project],
  );

  const retryWorkflow = () => {
    if (workflowError?.action === "upload") {
      void runUpload();
    } else if (workflowError?.action === "generate") {
      void runGeneration();
    }
  };

  const quickReport =
    lastQuickReport ??
    (project?.audit_report?.audit_status === "quick_complete"
      ? project.audit_report
      : null);
  const deepReport =
    project?.audit_report?.audit_status === "deep_complete" ? project.audit_report : null;

  const evidenceBySentence = useMemo(() => {
    const result = new Map<string, EvidenceRecord>();
    if (!project) {
      return result;
    }
    const sentenceByClaim = new Map(
      project.claims.map((claim) => [claim.claim_id, claim.sentence_id]),
    );
    for (const evidence of project.evidence_records) {
      const sentenceId = sentenceByClaim.get(evidence.claim_id);
      if (
        sentenceId &&
        evidence.quote_verified &&
        evidence.page_index !== null &&
        evidence.quote
      ) {
        result.set(sentenceId, evidence);
      }
    }
    return result;
  }, [project]);

  const selectSentence = useCallback(
    (sentenceId: string) => {
      setSelectedSentenceId(sentenceId);
      const evidence = evidenceBySentence.get(sentenceId) ?? null;
      setSelectedEvidence(evidence);
      if (evidence?.page_index !== null && evidence?.quote) {
        setPdfTarget({ pageIndex: evidence.page_index, quote: evidence.quote });
      } else {
        setPdfTarget(null);
      }
      setMobilePanel("evidence");
    },
    [evidenceBySentence],
  );

  const evidenceInsufficient = Boolean(
    project?.document &&
      (project.claims.length === 0 || evidenceBySentence.size === 0),
  );
  const modelMode = project?.model_mode ?? null;

  let statusTitle = "未上传";
  let statusDetail = "选择单篇文本型 PDF，并确认拥有处理权限。";
  let statusTone = "neutral";
  if (operation === "parsing") {
    statusTitle = "解析中";
    statusDetail = "正在保存并解析 PDF，请勿关闭页面。";
    statusTone = "progress";
  } else if (operation === "generating") {
    statusTitle = "生成中";
    statusDetail = "正在生成五区解读并执行快速检查。";
    statusTone = "progress";
  } else if (auditState === "running") {
    statusTitle = "深度审计中";
    statusDetail = "快速检查结果已保留，正在运行完整审计。";
    statusTone = "progress";
  } else if (auditState === "failed") {
    statusTitle = "完整审计失败，可重试";
    statusDetail = "稳定版本和快速检查结果未被覆盖。";
    statusTone = "error";
  } else if (deepReport) {
    statusTitle = "完整审计完成";
    statusDetail = "可在右侧查看八维结果、风险与最终结论。";
    statusTone = "success";
  } else if (quickReport) {
    statusTitle = evidenceInsufficient ? "快速检查完成 · 证据不足" : "快速检查完成";
    statusDetail = "当前没有最终分数；可从右侧启动完整审计。";
    statusTone = evidenceInsufficient ? "warning" : "success";
  } else if (project?.stage === "parsed") {
    statusTitle = "解析完成";
    statusDetail = `共 ${project.parse_quality?.page_count ?? 0} 页、${project.source_block_count} 个来源块，等待生成。`;
    statusTone = "success";
  }
  if (workflowError) {
    statusTitle = "操作失败，可重试";
    statusDetail = workflowError.message;
    statusTone = "error";
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">PL</div>
          <div>
            <h1>PaperLens</h1>
            <p>可信学术解读工作台</p>
          </div>
        </div>
        <div className="header-status">
          <span className={`service-state service-state--${serviceState}`}>
            {serviceState === "online"
              ? `API ${serviceVersion ?? ""}`
              : serviceState === "offline"
                ? "API 未连接"
                : "检查 API"}
          </span>
          <span
            className={`mode-badge${modelMode ? ` mode-badge--${modelMode}` : ""}`}
          >
            {modelMode === "mock"
              ? "Mock"
              : modelMode === "live"
                ? "Live"
                : "模式待确认"}
          </span>
        </div>
      </header>

      <section className="command-bar" aria-label="项目操作">
        <div className="upload-controls">
          {!project ? (
            <>
              <label className="file-picker">
                <FileUp size={17} />
                <span>{file?.name ?? "选择 PDF"}</span>
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  aria-label="选择 PDF"
                  disabled={operation !== "idle"}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <label className="rights-check">
                <input
                  type="checkbox"
                  checked={rightsConfirmed}
                  disabled={operation !== "idle"}
                  onChange={(event) => setRightsConfirmed(event.target.checked)}
                />
                <span>确认拥有处理权限</span>
              </label>
              <button
                className="primary-button"
                type="button"
                disabled={!file || !rightsConfirmed || operation !== "idle"}
                onClick={() => void runUpload()}
              >
                {operation === "parsing" ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <FileUp size={16} />
                )}
                上传论文 PDF
              </button>
            </>
          ) : project.document ? (
            <div className="project-summary">
              <CheckCircle2 size={17} />
              <span>项目 {project.project_id}</span>
              <small>版本 {project.current_version_no ?? "—"}</small>
            </div>
          ) : (
            <>
              <div className="project-summary">
                <CheckCircle2 size={17} />
                <span>PDF 已解析</span>
                <small>{file?.name}</small>
              </div>
              <button
                className="primary-button"
                type="button"
                disabled={operation !== "idle"}
                onClick={() => void runGeneration()}
              >
                {operation === "generating" ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <Play size={16} />
                )}
                生成五区解读
              </button>
            </>
          )}
        </div>

        <div className={`workflow-status workflow-status--${statusTone}`} aria-live="polite">
          <div>
            <strong>{statusTitle}</strong>
            <span>{statusDetail}</span>
          </div>
          {workflowError?.retryable ? (
            <button type="button" onClick={retryWorkflow}>
              <RefreshCw size={15} />
              重试
            </button>
          ) : null}
        </div>
      </section>

      <nav className="mobile-tabs" role="tablist" aria-label="工作台面板">
        {(
          [
            ["pdf", "PDF"],
            ["document", "解读"],
            ["evidence", "证据审计"],
          ] as const
        ).map(([panel, label]) => (
          <button
            type="button"
            role="tab"
            key={panel}
            aria-selected={mobilePanel === panel}
            className={mobilePanel === panel ? "active" : ""}
            onClick={() => setMobilePanel(panel)}
          >
            {label}
          </button>
        ))}
      </nav>

      <section className="workspace" aria-label="三栏学术工作台">
        <div
          className={`workspace-column workspace-column--pdf${
            mobilePanel === "pdf" ? " workspace-column--active" : ""
          }`}
          role="tabpanel"
          aria-label="PDF 面板"
        >
          <PdfPane pdfUrl={pdfUrl} target={pdfTarget} />
        </div>
        <div
          className={`workspace-column workspace-column--document${
            mobilePanel === "document" ? " workspace-column--active" : ""
          }`}
          role="tabpanel"
          aria-label="解读面板"
        >
          <DocumentPane
            document={project?.document ?? null}
            selectedSentenceId={selectedSentenceId}
            evidenceSentenceIds={new Set(evidenceBySentence.keys())}
            onSelectSentence={selectSentence}
          />
        </div>
        <div
          className={`workspace-column workspace-column--evidence${
            mobilePanel === "evidence" ? " workspace-column--active" : ""
          }`}
          role="tabpanel"
          aria-label="证据审计面板"
        >
          <SidePanel
            selectedSentenceId={selectedSentenceId}
            selectedEvidence={selectedEvidence}
            evidenceInsufficient={evidenceInsufficient}
            quickReport={quickReport}
            deepReport={deepReport}
            auditState={deepReport ? "complete" : auditState}
            auditError={auditError}
            versions={project?.versions ?? []}
            canAudit={Boolean(project && quickReport)}
            onRunAudit={(request) => void runAudit(request)}
          />
        </div>
      </section>
    </main>
  );
}
