import { CheckCircle2, FileUp, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  acceptRevision,
  auditProject,
  createProject,
  createRequestHandle,
  createRevision,
  exportProjectMarkdown,
  generateProject,
  getHealth,
  isDesktop,
  getRecentProject,
  setRecentProject,
  getProject,
  getProjectPdf,
  isRequestCancelled,
  rejectRevision,
  restoreProjectVersion,
  type RequestHandle,
} from "./api";
import { DesktopSettings } from "./components/DesktopSettings";
import { DocumentPane } from "./components/DocumentPane";
import { PdfPane, type PdfTarget } from "./components/PdfPane";
import {
  SidePanel,
  type AuditUiError,
  type AuditUiState,
  type RevisionUiState,
} from "./components/SidePanel";
import type {
  AuditReport,
  DeepAuditRequest,
  EditPatch,
  EvidenceRecord,
  PatchScope,
  ProjectView,
} from "./types";


type ProjectOperation = "idle" | "parsing" | "generating" | "opening";
type RetryAction = "upload" | "generate";
type MobilePanel = "pdf" | "document" | "evidence";
type RevisionRefreshReason = "completed_write" | "target_stale";
type RevisionRetry =
  | {
      action: "preview";
      input: { scope: PatchScope; userInstruction: string };
    }
  | { action: "accept" }
  | { action: "reject" }
  | { action: "restore"; versionId: string }
  | {
      action: "reconcile";
      mutation: RevisionMutation;
      baseVersionId: string | null;
    }
  | { action: "refresh"; reason: RevisionRefreshReason }
  | { action: "export" };

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


function toCompletedWriteRefreshError(error: unknown): AuditUiError {
  if (isRequestCancelled(error)) {
    return {
      code: "NETWORK_ERROR",
      message: "写操作已完成，但当前项目刷新失败。请重试读取当前版本。",
      retryable: true,
    };
  }
  const uiError = toUiError(error);
  return {
    ...uiError,
    message: `写操作已完成，但当前项目刷新失败。${uiError.message}`,
  };
}


function toTargetStaleRefreshError(error: unknown): AuditUiError {
  const uiError = isRequestCancelled(error)
    ? {
        code: "NETWORK_ERROR",
        message: "读取服务端当前版本被中断。",
        retryable: true,
      }
    : toUiError(error);
  return {
    ...uiError,
    message: `当前修订目标已过期，读取服务端当前版本失败。${uiError.message}`,
  };
}


type RevisionMutation = "preview" | "accept" | "restore";


const definiteWriteRejections: Readonly<
  Record<RevisionMutation, Readonly<Record<number, ReadonlySet<string>>>>
> = {
  preview: {
    404: new Set(["PROJECT_NOT_FOUND"]),
    409: new Set(["PROJECT_NOT_READY"]),
    422: new Set(["PATCH_INVALID"]),
  },
  accept: {
    404: new Set(["PATCH_NOT_FOUND", "PROJECT_NOT_FOUND"]),
    409: new Set(["TARGET_STALE", "PROJECT_NOT_READY"]),
    422: new Set(["PATCH_INVALID"]),
  },
  restore: {
    404: new Set(["VERSION_NOT_FOUND", "PROJECT_NOT_FOUND"]),
    409: new Set(["PROJECT_NOT_READY", "IDEMPOTENCY_CONFLICT"]),
    422: new Set(["IDEMPOTENCY_KEY_INVALID"]),
  },
};


function isDefiniteWriteRejection(
  mutation: RevisionMutation,
  error: unknown,
): error is ApiClientError {
  return (
    error instanceof ApiClientError &&
    definiteWriteRejections[mutation][error.status]?.has(error.errorCode) === true
  );
}


function toUncertainWriteError(
  error: unknown,
  reconciliationFailed = false,
): AuditUiError {
  const code = isRequestCancelled(error)
    ? "NETWORK_ERROR"
    : error instanceof ApiClientError
      ? error.errorCode
      : "UNKNOWN_ERROR";
  return {
    code,
    message: reconciliationFailed
      ? "写入结果仍未确认，读取服务端当前版本未完成。请再次读取。"
      : "写入结果尚未确认，需要读取服务端当前版本。",
    retryable: true,
  };
}


export function App() {
  const [desktop] = useState(isDesktop);
  const [recovering, setRecovering] = useState(desktop);
  const [projectIdInput, setProjectIdInput] = useState("");
  const [recoveryError, setRecoveryError] = useState<AuditUiError | null>(null);
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
  const [pendingPatch, setPendingPatch] = useState<EditPatch | null>(null);
  const [revisionState, setRevisionState] = useState<RevisionUiState>("idle");
  const [revisionError, setRevisionError] = useState<AuditUiError | null>(null);
  const [revisionRetry, setRevisionRetry] = useState<RevisionRetry | null>(null);

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

  const resetSelection = useCallback(() => {
    setSelectedSentenceId(null);
    setSelectedEvidence(null);
    setPdfTarget(null);
  }, []);

  const applyRefreshedProject = useCallback(
    (nextProject: ProjectView) => {
      setProject(nextProject);
      setLastQuickReport(
        nextProject.audit_report?.audit_status === "quick_complete"
          ? nextProject.audit_report
          : null,
      );
      setAuditState(
        nextProject.audit_report?.audit_status === "deep_complete"
          ? "complete"
          : "idle",
      );
      setAuditError(null);
      setPendingPatch(nextProject.pending_patch);
      setRevisionState(
        nextProject.stage === "patch_pending" ? "preview_ready" : "idle",
      );
      setRevisionError(null);
      setRevisionRetry(null);
      resetSelection();
      setMobilePanel("document");
    },
    [resetSelection],
  );

  const rememberProject = useCallback(async (id: string) => {
    if (!desktop) return;
    try { await setRecentProject(id); }
    catch (error) {
      setRecoveryError({ ...toUiError(error), message: `项目已打开，但最近项目未保存。${toUiError(error).message}` });
    }
  }, [desktop]);

  const openProject = useCallback(async (id: string) => {
    const projectId = id.trim();
    if (!projectId || projectId.length > 100) {
      setRecoveryError({ code: "DESKTOP_STATE_INVALID", message: "请输入 1 至 100 个字符的项目 ID。", retryable: false });
      return;
    }
    const request = beginRequest();
    setOperation("opening");
    setRecoveryError(null);
    try {
      const [nextProject, blob] = await Promise.all([
        getProject(projectId, request.signal), getProjectPdf(projectId, request.signal),
      ]);
      if (request.signal.aborted) return;
      replacePdfUrl(blob);
      applyRefreshedProject(nextProject);
      setWorkflowError(null);
      setFile(null);
      setProjectIdInput(projectId);
      await rememberProject(projectId);
    } catch (error) {
      if (!isRequestCancelled(error)) setRecoveryError(toUiError(error));
    } finally {
      if (!request.signal.aborted) setOperation("idle");
      finishRequest(request);
    }
  }, [applyRefreshedProject, beginRequest, finishRequest, rememberProject, replacePdfUrl]);

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    getRecentProject().then(async ({ project_id }) => {
      if (active && project_id !== null) await openProject(project_id);
    }).catch((error: unknown) => {
      if (active) setRecoveryError(toUiError(error));
    }).finally(() => { if (active) setRecovering(false); });
    return () => { active = false; };
  }, [desktop, openProject]);

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
    setPendingPatch(null);
    setRevisionState("idle");
    setRevisionError(null);
    setRevisionRetry(null);

    try {
      const created = await createProject(file, request.signal);
      const [nextProject, pdfBlob] = await Promise.all([
        getProject(created.project_id, request.signal),
        getProjectPdf(created.project_id, request.signal),
      ]);
      applyRefreshedProject(nextProject);
      replacePdfUrl(pdfBlob);
      await rememberProject(created.project_id);
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
  }, [
    applyRefreshedProject,
    beginRequest,
    file,
    finishRequest,
    replacePdfUrl,
    rightsConfirmed,
    rememberProject,
  ]);

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
      const generated = await generateProject(
        project.project_id,
        { claim_policy: "required" },
        request.signal,
      );
      setLastQuickReport(generated.quick_report ?? null);
      const nextProject = await getProject(project.project_id, request.signal);
      applyRefreshedProject(nextProject);
      setOperation("idle");
    } catch (error: unknown) {
      if (isRequestCancelled(error)) {
        return;
      }
      setOperation("idle");
      setWorkflowError({ ...toUiError(error), action: "generate" });
    } finally {
      finishRequest(request);
    }
  }, [applyRefreshedProject, beginRequest, finishRequest, project]);

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
        await auditProject(project.project_id, input, request.signal);
        const nextProject = await getProject(project.project_id, request.signal);
        applyRefreshedProject(nextProject);
        if (nextProject.audit_report?.audit_status === "deep_complete") {
          setMobilePanel("evidence");
        }
      } catch (error: unknown) {
        if (isRequestCancelled(error)) {
          setAuditState("idle");
          return;
        }
        setAuditError(toUiError(error));
        setAuditState("failed");
      } finally {
        finishRequest(request);
      }
    },
    [applyRefreshedProject, beginRequest, finishRequest, project],
  );

  const runRevisionPreview = useCallback(
    async (input: { scope: PatchScope; userInstruction: string }) => {
      if (!project?.current_version_id) {
        return;
      }
      const targetSentenceId = input.scope === "sentence" ? selectedSentenceId : null;
      if (input.scope === "sentence" && !targetSentenceId) {
        return;
      }
      const request = beginRequest();
      setRevisionState("previewing");
      setRevisionError(null);
      setRevisionRetry({ action: "preview", input });
      try {
        const patch = await createRevision(
          project.project_id,
          {
            base_version_id: project.current_version_id,
            scope: input.scope,
            target_sentence_id: targetSentenceId,
            user_instruction: input.userInstruction,
          },
          request.signal,
        );
        setPendingPatch(patch);
        setProject((currentProject) =>
          currentProject
            ? {
                ...currentProject,
                stage: "patch_pending",
                pending_patch: patch,
              }
            : currentProject,
        );
        setRevisionState("preview_ready");
        setRevisionRetry(null);
      } catch (error: unknown) {
        if (
          error instanceof ApiClientError &&
          error.status === 409 &&
          error.errorCode === "TARGET_STALE"
        ) {
          setPendingPatch(null);
          setRevisionState("refreshing");
          setRevisionError(null);
          setRevisionRetry({ action: "refresh", reason: "target_stale" });
          try {
            const nextProject = await getProject(project.project_id, request.signal);
            applyRefreshedProject(nextProject);
          } catch (refreshError: unknown) {
            setRevisionError(toTargetStaleRefreshError(refreshError));
            setRevisionState("refresh_failed");
            setRevisionRetry({ action: "refresh", reason: "target_stale" });
          }
          return;
        }
        if (!isDefiniteWriteRejection("preview", error)) {
          setRevisionError(toUncertainWriteError(error));
          setRevisionState("write_uncertain");
          setRevisionRetry({
            action: "reconcile",
            mutation: "preview",
            baseVersionId: project.current_version_id,
          });
          return;
        }
        setRevisionError(toUiError(error));
        setRevisionState("failed");
      } finally {
        finishRequest(request);
      }
    },
    [applyRefreshedProject, beginRequest, finishRequest, project, selectedSentenceId],
  );

  const runAcceptRevision = useCallback(async () => {
    if (!project || !pendingPatch) {
      return;
    }
    const request = beginRequest();
    setRevisionState("accepting");
    setRevisionError(null);
    setRevisionRetry({ action: "accept" });
    const baseVersionId = project.current_version_id;
    let writeCompleted = false;
    try {
      await acceptRevision(
        project.project_id,
        pendingPatch.patch_id,
        request.signal,
      );
      writeCompleted = true;
      setPendingPatch(null);
      setRevisionState("refreshing");
      setRevisionRetry({ action: "refresh", reason: "completed_write" });
      const nextProject = await getProject(project.project_id, request.signal);
      applyRefreshedProject(nextProject);
    } catch (error: unknown) {
      if (writeCompleted) {
        setRevisionError(toCompletedWriteRefreshError(error));
        setRevisionState("refresh_failed");
        setPendingPatch(null);
        setRevisionRetry({ action: "refresh", reason: "completed_write" });
        return;
      }
      if (!isDefiniteWriteRejection("accept", error)) {
        setRevisionError(toUncertainWriteError(error));
        setRevisionState("write_uncertain");
        setRevisionRetry({
          action: "reconcile",
          mutation: "accept",
          baseVersionId,
        });
        return;
      }
      setRevisionError(toUiError(error));
      setRevisionState("failed");
    } finally {
      finishRequest(request);
    }
  }, [applyRefreshedProject, beginRequest, finishRequest, pendingPatch, project]);

  const runRestoreVersion = useCallback(
    async (versionId: string) => {
      if (!project) {
        return;
      }
      const idempotencyKey = crypto.randomUUID();
      const request = beginRequest();
      setRevisionState("restoring");
      setRevisionError(null);
      setRevisionRetry({ action: "restore", versionId });
      const baseVersionId = project.current_version_id;
      let writeCompleted = false;
      try {
        await restoreProjectVersion(
          project.project_id,
          versionId,
          idempotencyKey,
          request.signal,
        );
        writeCompleted = true;
        setRevisionState("refreshing");
        setRevisionRetry({ action: "refresh", reason: "completed_write" });
        const nextProject = await getProject(project.project_id, request.signal);
        applyRefreshedProject(nextProject);
      } catch (error: unknown) {
        if (writeCompleted) {
          setRevisionError(toCompletedWriteRefreshError(error));
          setRevisionState("refresh_failed");
          setRevisionRetry({ action: "refresh", reason: "completed_write" });
          return;
        }
        if (!isDefiniteWriteRejection("restore", error)) {
          setRevisionError(toUncertainWriteError(error));
          setRevisionState("write_uncertain");
          setRevisionRetry({
            action: "reconcile",
            mutation: "restore",
            baseVersionId,
          });
          return;
        }
        setRevisionError(toUiError(error));
        setRevisionState("failed");
      } finally {
        finishRequest(request);
      }
    },
    [applyRefreshedProject, beginRequest, finishRequest, project],
  );

  const runWriteReconciliation = useCallback(
    async (
      mutation: RevisionMutation,
      baseVersionId: string | null,
    ) => {
      if (!project) {
        return;
      }
      const request = beginRequest();
      setRevisionState("reconciling");
      setRevisionError(null);
      setRevisionRetry({ action: "reconcile", mutation, baseVersionId });
      try {
        const nextProject = await getProject(project.project_id, request.signal);
        applyRefreshedProject(nextProject);
      } catch (error: unknown) {
        setRevisionError(toUncertainWriteError(error, true));
        setRevisionState("reconcile_failed");
        setRevisionRetry({ action: "reconcile", mutation, baseVersionId });
      } finally {
        finishRequest(request);
      }
    },
    [applyRefreshedProject, beginRequest, finishRequest, project],
  );

  const runRevisionRefresh = useCallback(
    async (reason: RevisionRefreshReason) => {
      if (!project) {
        return;
      }
      const request = beginRequest();
      setRevisionState("refreshing");
      setRevisionError(null);
      setRevisionRetry({ action: "refresh", reason });
      try {
        const nextProject = await getProject(project.project_id, request.signal);
        applyRefreshedProject(nextProject);
      } catch (error: unknown) {
        setRevisionError(
          reason === "target_stale"
            ? toTargetStaleRefreshError(error)
            : toCompletedWriteRefreshError(error),
        );
        setRevisionState("refresh_failed");
        setRevisionRetry({ action: "refresh", reason });
      } finally {
        finishRequest(request);
      }
    },
    [applyRefreshedProject, beginRequest, finishRequest, project],
  );

  const runExport = useCallback(async () => {
    if (!project) {
      return;
    }
    const request = beginRequest();
    setRevisionState("exporting");
    setRevisionError(null);
    setRevisionRetry({ action: "export" });
    try {
      const markdown = await exportProjectMarkdown(project.project_id, request.signal);
      const downloadUrl = URL.createObjectURL(markdown);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = `paperlens-${project.project_id}.md`;
      link.click();
      URL.revokeObjectURL(downloadUrl);
      setRevisionState("idle");
      setRevisionRetry(null);
    } catch (error: unknown) {
      if (isRequestCancelled(error)) {
        setRevisionState("idle");
        setRevisionRetry(null);
        return;
      }
      setRevisionError(toUiError(error));
      setRevisionState("failed");
    } finally {
      finishRequest(request);
    }
  }, [beginRequest, finishRequest, project]);

  const runRejectRevision = useCallback(async () => {
    if (!project || !pendingPatch) {
      return;
    }
    const request = beginRequest();
    setRevisionState("rejecting");
    setRevisionError(null);
    setRevisionRetry({ action: "reject" });
    let writeCompleted = false;
    try {
      await rejectRevision(project.project_id, pendingPatch.patch_id, request.signal);
      writeCompleted = true;
      setRevisionState("refreshing");
      setRevisionRetry({ action: "refresh", reason: "completed_write" });
      const nextProject = await getProject(project.project_id, request.signal);
      applyRefreshedProject(nextProject);
    } catch (error: unknown) {
      if (writeCompleted) {
        setRevisionError(toCompletedWriteRefreshError(error));
        setRevisionState("refresh_failed");
        setRevisionRetry({ action: "refresh", reason: "completed_write" });
        return;
      }
      if (isRequestCancelled(error)) {
        setRevisionState("preview_ready");
        setRevisionRetry(null);
        return;
      }
      setRevisionError(toUiError(error));
      setRevisionState("failed");
    } finally {
      finishRequest(request);
    }
  }, [applyRefreshedProject, beginRequest, finishRequest, pendingPatch, project]);

  const retryRevision = useCallback(() => {
    if (revisionRetry?.action === "preview") {
      void runRevisionPreview(revisionRetry.input);
    } else if (revisionRetry?.action === "accept") {
      void runAcceptRevision();
    } else if (revisionRetry?.action === "reject") {
      void runRejectRevision();
    } else if (revisionRetry?.action === "restore") {
      void runRestoreVersion(revisionRetry.versionId);
    } else if (revisionRetry?.action === "reconcile") {
      void runWriteReconciliation(
        revisionRetry.mutation,
        revisionRetry.baseVersionId,
      );
    } else if (revisionRetry?.action === "refresh") {
      void runRevisionRefresh(revisionRetry.reason);
    } else if (revisionRetry?.action === "export") {
      void runExport();
    }
  }, [
    revisionRetry,
    runAcceptRevision,
    runExport,
    runRejectRevision,
    runRevisionRefresh,
    runRestoreVersion,
    runWriteReconciliation,
    runRevisionPreview,
  ]);

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

  const projectSwitchLocked = recovering || operation !== "idle" || auditState === "running" || revisionState !== "idle";

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

      {desktop && <DesktopSettings />}
      {desktop && (
        <section className="command-bar" aria-label="打开已有项目">
          <label>已有项目 ID <input aria-label="已有项目 ID" value={projectIdInput} maxLength={100}
            disabled={projectSwitchLocked} onChange={(event) => setProjectIdInput(event.target.value)} /></label>
          <button type="button" disabled={projectSwitchLocked} onClick={() => void openProject(projectIdInput)}>打开项目</button>
          {project && <label>当前项目 ID <input aria-label="当前项目 ID" readOnly value={project.project_id}
            onFocus={(event) => event.target.select()} /></label>}
          {(recovering || operation === "opening") && <span role="status">正在恢复项目…</span>}
          {recoveryError && <span role="alert">项目恢复提示：{recoveryError.message}（{recoveryError.code}）</span>}
        </section>
      )}
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
                  disabled={recovering || operation !== "idle"}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <label className="rights-check">
                <input
                  type="checkbox"
                  checked={rightsConfirmed}
                  disabled={recovering || operation !== "idle"}
                  onChange={(event) => setRightsConfirmed(event.target.checked)}
                />
                <span>确认拥有处理权限</span>
              </label>
              <button
                className="primary-button"
                type="button"
                disabled={recovering || !file || !rightsConfirmed || operation !== "idle"}
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
                disabled={recovering || operation !== "idle"}
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

      <section className="workspace" aria-label="三栏学术工作台" inert={recovering || operation === "opening"}>
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
            canAudit={Boolean(project && quickReport && !pendingPatch)}
            onRunAudit={(request) => void runAudit(request)}
            currentVersionId={project?.current_version_id ?? null}
            pendingPatch={pendingPatch}
            revisionState={revisionState}
            revisionError={revisionError}
            revisionRefreshReason={
              revisionRetry?.action === "refresh" ? revisionRetry.reason : null
            }
            onCreateRevision={(input) => void runRevisionPreview(input)}
            onAcceptRevision={() => void runAcceptRevision()}
            onRejectRevision={() => void runRejectRevision()}
            onRetryRevision={retryRevision}
            onRestoreVersion={(versionId) => void runRestoreVersion(versionId)}
            onExportProject={() => void runExport()}
          />
        </div>
      </section>
    </main>
  );
}
