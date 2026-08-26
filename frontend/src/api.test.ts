import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  auditProject,
  createProject,
  createRequestHandle,
  generateProject,
  getProject,
  getProjectPdf,
  isRequestCancelled,
} from "./api";


const API_BASE_URL = "http://127.0.0.1:8000";


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
