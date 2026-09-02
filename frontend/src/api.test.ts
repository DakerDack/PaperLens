import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  acceptRevision,
  auditProject,
  createProject,
  createRequestHandle,
  createRevision,
  exportProjectMarkdown,
  generateProject,
  getProject,
  getProjectPdf,
  isRequestCancelled,
  rejectRevision,
  restoreProjectVersion,
} from "./api";
import type { RestoreVersionResponse } from "./types";


const API_BASE_URL = "http://127.0.0.1:8000";
const RESTORE_KEY = "123e4567-e89b-42d3-a456-426614174000";


afterEach(() => {
  vi.unstubAllGlobals();
});


describe("PaperLens API client", () => {
  it("owns every stage-four project URL and request shape", async () => {
    const pdf = new File(["%PDF-test"], "paper.pdf", { type: "application/pdf" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ project_id: "p-1", stage: "parsed" }, { status: 201 }),
      )
      .mockResolvedValueOnce(Response.json({ project_id: "p-1" }))
      .mockResolvedValueOnce(
        new Response(new Blob(["%PDF-test"], { type: "application/pdf" }), {
          headers: { "content-type": "application/pdf" },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({ project_id: "p-1", stage: "quick_checked" }),
      )
      .mockResolvedValueOnce(
        Response.json({ project_id: "p-1", stage: "deep_audited" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await createProject(pdf);
    await getProject("p-1");
    await getProjectPdf("p-1");
    await generateProject("p-1", { claim_policy: "required" });
    await auditProject("p-1", {
      source_disclosure_status: "present",
      ai_assistance_disclosure_status: "present",
      generated_content_label_applicability: "not_applicable",
      generated_content_label_status: "not_applicable",
    });

    const uploadInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const uploadBody = uploadInit.body as FormData;
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${API_BASE_URL}/api/projects`);
    expect(uploadInit.method).toBe("POST");
    expect(uploadBody.get("file")).toBe(pdf);
    expect(uploadBody.get("rights_confirmed")).toBe("true");
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`${API_BASE_URL}/api/projects/p-1`);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(`${API_BASE_URL}/api/projects/p-1/pdf`);
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/generate`,
    );
    expect(fetchMock.mock.calls[3]?.[1]).toMatchObject({
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ claim_policy: "required" }),
    });
    expect(fetchMock.mock.calls[4]?.[0]).toBe(`${API_BASE_URL}/api/projects/p-1/audit`);
  });

  it("keeps stable server errors and retryability", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            error_code: "AUDIT_INCOMPLETE",
            message: "完整审计结果不完整，请重试。",
            retryable: true,
            details: { boundary: "HY3_RISK_CATEGORY_COVERAGE_INVALID" },
          },
          { status: 502 },
        ),
      ),
    );

    const error = await getProject("p-1").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      status: 502,
      errorCode: "AUDIT_INCOMPLETE",
      retryable: true,
      details: { boundary: "HY3_RISK_CATEGORY_COVERAGE_INVALID" },
    });
  });

  it("owns every frozen stage-six URL and request shape", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ patch_id: "patch-1" }))
      .mockResolvedValueOnce(Response.json({ version_id: "v-2" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        Response.json({
          version_id: "v-3",
          version_no: 3,
          parent_version_id: "v-2",
          reason: "restore:v-1",
          created_at: "2026-08-31T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
        new Response("# Current document", {
          headers: { "content-type": "text/markdown; charset=utf-8" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const revision = {
      base_version_id: "v-1",
      scope: "sentence" as const,
      target_sentence_id: "s-1",
      user_instruction: "简化这一句。",
    };

    await createRevision("p-1", revision);
    await acceptRevision("p-1", "patch-1");
    await rejectRevision("p-1", "patch-1");
    const restoreController = new AbortController();
    const restored: RestoreVersionResponse = await restoreProjectVersion(
      "p-1",
      "v-1",
      RESTORE_KEY,
      restoreController.signal,
    );
    const markdown = await exportProjectMarkdown("p-1");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/revisions`,
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(revision),
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/revisions/patch-1/accept`,
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/revisions/patch-1/reject`,
    );
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/versions/v-1/restore`,
    );
    expect(fetchMock.mock.calls[3]?.[1]).toMatchObject({
      method: "POST",
      headers: { "Idempotency-Key": RESTORE_KEY },
      signal: restoreController.signal,
    });
    expect(fetchMock.mock.calls[3]?.[1]?.body).toBeUndefined();
    expect(restored).toEqual({
      version_id: "v-3",
      version_no: 3,
      parent_version_id: "v-2",
      reason: "restore:v-1",
      created_at: "2026-08-31T00:00:00Z",
    });
    expect(fetchMock.mock.calls[4]?.[0]).toBe(
      `${API_BASE_URL}/api/projects/p-1/export`,
    );
    expect(await markdown.text()).toBe("# Current document");
  });

  it.each([
    [404, "PATCH_NOT_FOUND"],
    [422, "PATCH_INVALID"],
  ])("preserves reject errors %i/%s", async (status, errorCode) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            error_code: errorCode,
            message: "The revision patch could not be rejected.",
            retryable: false,
            details: null,
          },
          { status },
        ),
      ),
    );

    const error = await rejectRevision("p-1", "patch-1").catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      status,
      errorCode,
      retryable: false,
    });
  });

  it.each([
    [422, "IDEMPOTENCY_KEY_INVALID"],
    [409, "IDEMPOTENCY_CONFLICT"],
  ])("preserves restore idempotency errors %i/%s", async (status, errorCode) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            error_code: errorCode,
            message: "The restore request was rejected.",
            retryable: false,
            details: null,
          },
          { status },
        ),
      ),
    );

    const error = await restoreProjectVersion(
      "p-1",
      "v-1",
      RESTORE_KEY,
    ).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      status,
      errorCode,
      retryable: false,
    });
  });

  it("requires an empty 204 reject response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));

    const error = await rejectRevision("p-1", "patch-1").catch(
      (reason: unknown) => reason,
    );

    expect(error).toMatchObject({
      status: 200,
      errorCode: "INVALID_RESPONSE",
      retryable: false,
    });
  });

  it("maps transport failures and exposes request cancellation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const networkError = await getProject("p-1").catch((reason: unknown) => reason);

    expect(networkError).toMatchObject({
      status: 0,
      errorCode: "NETWORK_ERROR",
      retryable: true,
    });

    const request = createRequestHandle();
    request.cancel();
    expect(request.signal.aborted).toBe(true);
    expect(isRequestCancelled(new DOMException("aborted", "AbortError"))).toBe(true);
  });
});
