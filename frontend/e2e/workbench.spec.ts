import { expect, test, type Page } from "@playwright/test";
import path from "node:path";


const pdfPath = path.resolve(
  process.cwd(),
  "..",
  "backend",
  "tests",
  "fixtures",
  "simple_2page.pdf",
);

const documentDraft = {
  title: "PaperLens synthetic fixture explanation",
  sections: [
    {
      section_id: "research_question",
      heading: "Research question",
      sentences: [
        {
          sentence_id: "s-001",
          text: "The fixture identifies the first page as page one.",
        },
      ],
    },
    {
      section_id: "methods",
      heading: "Methods and data",
      sentences: [
        {
          sentence_id: "s-002",
          text: "The first page states that its text is synthetic and contains no private paper content.",
        },
      ],
    },
    {
      section_id: "results",
      heading: "Main result",
      sentences: [
        {
          sentence_id: "s-003",
          text: "The fixture identifies the second page as page two.",
        },
      ],
    },
    {
      section_id: "limitations",
      heading: "Limitation",
      sentences: [
        {
          sentence_id: "s-004",
          text: "The second page is used for zero-based page mapping.",
        },
      ],
    },
    {
      section_id: "plain_explanation",
      heading: "Plain explanation",
      sentences: [
        {
          sentence_id: "s-005",
          text: "In plain terms, this is a short synthetic PDF for parser tests.",
        },
      ],
    },
  ],
};

const claims = [
  {
    claim_id: "c-003",
    sentence_id: "s-003",
    text: "The fixture identifies the second page as page two.",
    claim_type: "result",
    importance: "critical",
    qualifiers: ["second page"],
    numeric_entities: [],
    auditability: "auditable",
    candidate_block_ids: ["p02-b001"],
    candidate_quote: "PaperLens fixture - page two",
  },
];

const evidenceRecords = [
  {
    claim_id: "c-003",
    block_id: "p02-b001",
    page_index: 1,
    quote: "PaperLens fixture - page two",
    bbox: [0.112, 0.074, 0.413, 0.095],
    match_method: "model_candidate",
    quote_verified: true,
    rule_flags: [],
  },
];

const quickReport = {
  audit_status: "quick_complete",
  dimensions: [],
  risk_assessment: null,
  hard_failures: [],
  core_gate_passed: null,
  overall_score: null,
  decision: "pending_deep_audit",
};

const dimensionIds = [
  "factual_consistency",
  "citation_correctness",
  "citation_completeness",
  "method_scope",
  "conclusion_limitations",
  "terminology",
  "reader_adaptation",
  "risk_compliance",
];

const dimensionLabels = [
  "事实一致性",
  "引文正确性",
  "引文完整性",
  "方法与范围",
  "结论与局限",
  "术语准确性",
  "读者适配",
  "风险与合规",
];

const riskLabels = ["敏感信息", "作者身份冒充", "学术诚信"];

const deepReport = {
  audit_status: "deep_complete",
  dimensions: dimensionIds.map((dimension_id) => ({
    dimension_id,
    raw_metrics: dimension_id === "risk_compliance" ? { level_points: 4 } : { level_points: 4 },
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
    risk_findings: [
      {
        category: "sensitive_information",
        status: "not_detected",
        locations: [],
        reason: "No sensitive-content risk was detected.",
        remediation: "No sensitive-content remediation is required.",
      },
      {
        category: "author_impersonation",
        status: "not_detected",
        locations: [],
        reason: "未发现作者身份冒充。",
        remediation: "无需修改。",
      },
      {
        category: "academic_integrity",
        status: "not_detected",
        locations: [],
        reason: "未发现鼓励违反学术诚信的内容。",
        remediation: "无需修改。",
      },
    ],
    level_points: 4,
  },
  hard_failures: [],
  core_gate_passed: true,
  overall_score: 100,
  decision: "qualified",
};

interface ApiPreset {
  emptyEvidence?: boolean;
  auditIncomplete?: boolean;
  mode?: "mock" | "live";
}


async function installApiPreset(page: Page, preset: ApiPreset = {}) {
  let stage: "parsed" | "quick_checked" | "deep_audited" = "parsed";
  let auditAttempts = 0;
  const mode = preset.mode ?? "mock";
  const activeClaims = preset.emptyEvidence ? [] : claims;
  const activeEvidence = preset.emptyEvidence ? [] : evidenceRecords;

  const projectView = () => ({
    project_id: "p-e2e",
    stage,
    model_mode: mode,
    parse_quality: {
      page_count: 2,
      block_count: 4,
      empty_page_rate: 0,
      abnormal_character_rate: 0,
      page_number_completeness_rate: 1,
      bbox_availability_rate: 1,
    },
    source_block_count: 4,
    current_version_id: stage === "parsed" ? null : "v-e2e-1",
    current_version_no: stage === "parsed" ? null : 1,
    document: stage === "parsed" ? null : documentDraft,
    claims: stage === "parsed" ? [] : activeClaims,
    evidence_records: stage === "parsed" ? [] : activeEvidence,
    audit_report:
      stage === "parsed" ? null : stage === "deep_audited" ? deepReport : quickReport,
    versions:
      stage === "parsed"
        ? []
        : [
            {
              version_id: "v-e2e-1",
              version_no: 1,
              parent_version_id: null,
              reason: "initial_generation",
              created_at: "2026-08-25T08:01:00Z",
            },
          ],
    error_code: null,
    retryable_stage: null,
    created_at: "2026-08-25T08:00:00Z",
    updated_at: "2026-08-25T08:01:00Z",
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const corsHeaders = {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "access-control-allow-headers": "content-type",
    };
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    if (url.pathname === "/api/health") {
      await route.fulfill({
        status: 200,
        json: { status: "ok", service: "paperlens-api", version: "0.1.0" },
        headers: corsHeaders,
      });
      return;
    }
    if (url.pathname === "/api/projects" && request.method() === "POST") {
      stage = "parsed";
      await route.fulfill({
        status: 201,
        json: {
          project_id: "p-e2e",
          stage: "parsed",
          parse_quality: projectView().parse_quality,
          source_block_count: 4,
          created_at: "2026-08-25T08:00:00Z",
        },
        headers: corsHeaders,
      });
      return;
    }
    if (url.pathname === "/api/projects/p-e2e/pdf") {
      await route.fulfill({
        status: 200,
        path: pdfPath,
        contentType: "application/pdf",
        headers: corsHeaders,
      });
      return;
    }
    if (url.pathname === "/api/projects/p-e2e" && request.method() === "GET") {
      await route.fulfill({ status: 200, json: projectView(), headers: corsHeaders });
      return;
    }
    if (
      url.pathname === "/api/projects/p-e2e/generate" &&
      request.method() === "POST"
    ) {
      stage = "quick_checked";
      await route.fulfill({
        status: 200,
        json: {
          project_id: "p-e2e",
          version_id: "v-e2e-1",
          stage: "quick_checked",
          model_mode: mode,
          document: documentDraft,
          claims: activeClaims,
          evidence_records: activeEvidence,
          quick_report: quickReport,
        },
        headers: corsHeaders,
      });
      return;
    }
    if (url.pathname === "/api/projects/p-e2e/audit" && request.method() === "POST") {
      auditAttempts += 1;
      if (preset.auditIncomplete) {
        await route.fulfill({
          status: 502,
          json: {
            error_code: "AUDIT_INCOMPLETE",
            message:
              "完整审计响应缺少所需风险类别，快速检查结果已经完整保留；请稍后重新尝试完整审计。该状态不会产生最终评分或结论，也不会把 Live 失败替换为 Mock 成功。",
            retryable: true,
            details: { boundary: "HY3_RISK_CATEGORY_COVERAGE_INVALID" },
          },
          headers: corsHeaders,
        });
        return;
      }
      stage = "deep_audited";
      await route.fulfill({
        status: 200,
        json: {
          project_id: "p-e2e",
          version_id: "v-e2e-1",
          stage: "deep_audited",
          audit_report: deepReport,
        },
        headers: corsHeaders,
      });
      return;
    }
    await route.fulfill({
      status: 404,
      json: {
        error_code: "PROJECT_NOT_FOUND",
        message: "Unexpected E2E route",
        retryable: false,
        details: null,
      },
      headers: corsHeaders,
    });
  });

  return { auditAttempts: () => auditAttempts };
}


async function uploadAndGenerate(page: Page) {
  await page.goto("/");
  await page.getByLabel("选择 PDF").setInputFiles(pdfPath);
  await page.getByRole("checkbox", { name: /确认拥有处理权限/ }).check();
  await page.getByRole("button", { name: "上传论文 PDF" }).click();
  await expect(page.getByRole("button", { name: "生成五区解读" })).toBeVisible();
  await page.getByRole("button", { name: "生成五区解读" }).click();
  await expect(page.getByText("PaperLens synthetic fixture explanation")).toBeVisible();
  await expect(page.getByText("快速检查完成").first()).toBeVisible();
}


async function expectCanvasInk(page: Page, pageNumber: number) {
  const canvas = page.locator(`canvas[aria-label="PDF 第 ${pageNumber} 页"]`);
  await expect(canvas).toBeVisible();
  await expect
    .poll(async () =>
      canvas.evaluate((element) => {
        const target = element as HTMLCanvasElement;
        if (target.width < 100 || target.height < 100) {
          return 0;
        }
        const context = target.getContext("2d");
        if (!context) {
          return 0;
        }
        const pixels = context.getImageData(0, 0, target.width, target.height).data;
        let ink = 0;
        for (let y = 0; y < target.height; y += 4) {
          for (let x = 0; x < target.width; x += 4) {
            const offset = (y * target.width + x) * 4;
            if (
              pixels[offset + 3] > 0 &&
              (pixels[offset] < 235 ||
                pixels[offset + 1] < 235 ||
                pixels[offset + 2] < 235)
            ) {
              ink += 1;
            }
          }
        }
        return ink;
      }),
    )
    .toBeGreaterThan(20);
}


async function expectShellInsideViewport(page: Page) {
  const geometry = await page.evaluate(() => {
    const shell = document.querySelector(".app-shell");
    const workspace = document.querySelector(".workspace");
    const visibleColumns = [...document.querySelectorAll(".workspace-column")].filter(
      (element) => getComputedStyle(element).display !== "none",
    );
    const box = (element: Element | null) => {
      const rect = element?.getBoundingClientRect();
      return rect
        ? { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }
        : null;
    };
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentWidth: document.documentElement.scrollWidth,
      shell: box(shell),
      workspace: box(workspace),
      columns: visibleColumns.map((column) => box(column)),
    };
  });
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewport.width + 1);
  expect(geometry.shell?.left).toBeGreaterThanOrEqual(0);
  expect(geometry.shell?.right).toBeLessThanOrEqual(geometry.viewport.width + 1);
  expect(geometry.workspace?.left).toBeGreaterThanOrEqual(0);
  expect(geometry.workspace?.right).toBeLessThanOrEqual(geometry.viewport.width + 1);
  for (const column of geometry.columns) {
    expect(column?.left).toBeGreaterThanOrEqual((geometry.workspace?.left ?? 0) - 1);
    expect(column?.right).toBeLessThanOrEqual((geometry.workspace?.right ?? 0) + 1);
  }
}


async function expectAuditResultRowsFit(page: Page) {
  const rows = page.locator(".audit-dimension-row, .risk-status-row");
  await expect(rows).toHaveCount(11);
  const geometry = await rows.evaluateAll((elements) =>
    elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      };
    }),
  );
  for (const row of geometry) {
    expect(row.scrollWidth).toBeLessThanOrEqual(row.clientWidth + 1);
    expect(row.left).toBeGreaterThanOrEqual(0);
    expect(row.right).toBeLessThanOrEqual(page.viewportSize()?.width ?? 0);
    expect(row.bottom).toBeGreaterThan(row.top);
  }
}


test("preset 1: upload and generate a five-section workbench at 1440x900", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installApiPreset(page);
  await uploadAndGenerate(page);

  await expect(page.getByRole("heading", { name: "Research question" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Methods and data" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Main result" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Limitation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Plain explanation" })).toBeVisible();
  await expect(page.getByText("Mock")).toBeVisible();
  await expectCanvasInk(page, 1);
  await expectShellInsideViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("desktop-upload-generate.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "运行完整审计" }).click();
  await expect(page.getByText("完整审计完成", { exact: true })).toBeVisible();
  const dimensionRegion = page.getByRole("region", { name: "八维审计结果" });
  const riskRegion = page.getByRole("region", { name: "结构化风险状态" });
  for (const label of dimensionLabels) {
    await expect(dimensionRegion.getByText(label, { exact: true })).toHaveCount(1);
  }
  await expect(dimensionRegion.getByText("分数 100.0", { exact: true })).toHaveCount(8);
  await expect(dimensionRegion.getByText("等级 good", { exact: true })).toHaveCount(8);
  for (const label of riskLabels) {
    await expect(riskRegion.getByText(label, { exact: true })).toHaveCount(1);
  }
  await expect(riskRegion.getByText("not_detected", { exact: true })).toHaveCount(3);
  await expect(page.getByText("总分 100.0", { exact: true })).toBeVisible();
  await expect(page.getByText("合格", { exact: true })).toBeVisible();
  await expectAuditResultRowsFit(page);
  await expectShellInsideViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("desktop-deep-audit-complete.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "证据审计", exact: true }).click();
  const mobilePanel = page.locator(".side-panel");
  const mobileGeometry = await mobilePanel.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(mobileGeometry.scrollHeight).toBeGreaterThan(mobileGeometry.clientHeight);
  expect(mobileGeometry.scrollWidth).toBeLessThanOrEqual(mobileGeometry.clientWidth + 1);
  await riskRegion.scrollIntoViewIfNeeded();
  await expect(riskRegion).toBeVisible();
  await expectAuditResultRowsFit(page);
  await expectShellInsideViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("mobile-deep-audit-complete.png"),
    fullPage: true,
  });
});


test("preset 2: one sentence click reaches page two and its excerpt", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installApiPreset(page);
  await uploadAndGenerate(page);

  await page
    .getByRole("button", {
      name: /The fixture identifies the second page as page two/,
    })
    .click();

  await expect(page.getByText("第 2 / 2 页")).toBeVisible();
  await expect(
    page.getByRole("region", { name: "句子证据" }).getByText("PaperLens fixture - page two"),
  ).toBeVisible();
  await expect(page.getByText("已在本页高亮证据摘录。")).toBeVisible();
  await expectCanvasInk(page, 2);
  await expectShellInsideViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("desktop-evidence-jump.png"),
    fullPage: true,
  });
});


test("preset 3: mobile empty evidence and AUDIT_INCOMPLETE remain retryable", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await installApiPreset(page, {
    emptyEvidence: true,
    auditIncomplete: true,
    mode: "live",
  });
  await uploadAndGenerate(page);

  await expect(page.getByText("Live")).toBeVisible();
  await page.getByRole("tab", { name: "证据审计" }).click();
  await expect(page.getByText("证据不足", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "运行完整审计" })).toBeVisible();
  await page.getByRole("button", { name: "运行完整审计" }).click();
  await expect(page.getByText("完整审计失败", { exact: true })).toBeVisible();
  await expect(page.getByText("快速检查结果已保留")).toBeVisible();
  await expect(page.getByText(/HY3_RISK_CATEGORY_COVERAGE_INVALID/)).toHaveCount(0);
  await expect(page.getByText(/总分/)).toHaveCount(0);
  await expect(page.getByText("合格", { exact: true })).toHaveCount(0);
  await expect(page.getByText("需要修改", { exact: true })).toHaveCount(0);
  await expect(page.getByText("不合格", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "八维审计结果" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "结构化风险状态" })).toHaveCount(0);
  await page.getByRole("button", { name: "重试完整审计" }).click();
  await expect.poll(state.auditAttempts).toBe(2);

  const errorFits = await page.locator(".inline-state--error").evaluate(
    (element) => element.scrollWidth <= element.clientWidth + 1,
  );
  expect(errorFits).toBe(true);

  for (const tab of ["PDF", "解读", "证据审计"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.locator(".workspace-column--active")).toBeVisible();
  }
  await page.getByRole("tab", { name: "PDF", exact: true }).click();
  await expectCanvasInk(page, 1);
  await expectShellInsideViewport(page);
  await page.getByRole("tab", { name: "证据审计" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("mobile-audit-incomplete.png"),
    fullPage: true,
  });
});
