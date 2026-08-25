import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PdfPane } from "./PdfPane";


const pdfMocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getPage: vi.fn(),
}));


vi.mock("pdfjs-dist", () => {
  class FakeTextLayer {
    readonly textDivs: HTMLElement[] = [];
    readonly textContentItemsStr: string[];
    private readonly container: HTMLElement;

    constructor({
      textContentSource,
      container,
    }: {
      textContentSource: { mockItems: string[] };
      container: HTMLElement;
    }) {
      this.container = container;
      this.textContentItemsStr = textContentSource.mockItems;
    }

    async render() {
      for (const text of this.textContentItemsStr) {
        const span = document.createElement("span");
        span.textContent = text;
        this.textDivs.push(span);
        this.container.append(span);
      }
    }

    cancel() {}
  }

  return {
    GlobalWorkerOptions: { workerSrc: "" },
    TextLayer: FakeTextLayer,
    getDocument: pdfMocks.getDocument,
  };
});

vi.mock("lucide-react", () => ({
  ChevronLeft: () => null,
  ChevronRight: () => null,
  ZoomIn: () => null,
  ZoomOut: () => null,
}));


afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});


function arrangePdf() {
  pdfMocks.getPage.mockImplementation(async (pageNumber: number) => ({
    getViewport: ({ scale }: { scale: number }) => ({
      width: 600 * scale,
      height: 800 * scale,
      transform: [scale, 0, 0, -scale, 0, 800 * scale],
    }),
    render: ({ canvas }: { canvas: HTMLCanvasElement }) => {
      canvas.dataset.renderedPage = String(pageNumber);
      return { promise: Promise.resolve(), cancel: vi.fn() };
    },
    streamTextContent: () => ({
      mockItems: pageNumber === 2 ? ["Evidence", " quote on page two"] : ["Other page"],
    }),
  }));
  pdfMocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 3, getPage: pdfMocks.getPage }),
    destroy: vi.fn(),
  });
}


describe("PdfPane", () => {
  it("moves to the evidence page and highlights text through the public text layer", async () => {
    arrangePdf();
    const onFindStatus = vi.fn();

    render(
      <PdfPane
        pdfUrl="blob:test-pdf"
        target={{ pageIndex: 1, quote: "Evidence quote" }}
        onFindStatus={onFindStatus}
      />,
    );

    await waitFor(() => expect(pdfMocks.getPage).toHaveBeenCalledWith(2));
    expect(screen.getByText("第 2 / 3 页")).toBeTruthy();
    await waitFor(() => expect(onFindStatus).toHaveBeenCalledWith("found"));
    expect(document.querySelector("canvas")?.dataset.renderedPage).toBe("2");
    expect(document.querySelectorAll(".text-highlight").length).toBeGreaterThan(0);
    expect(pdfMocks.getDocument).toHaveBeenCalledWith(
      expect.objectContaining({ isEvalSupported: false }),
    );
  });

  it("keeps the correct page visible when text lookup fails", async () => {
    arrangePdf();
    const onFindStatus = vi.fn();

    render(
      <PdfPane
        pdfUrl="blob:test-pdf"
        target={{ pageIndex: 1, quote: "missing excerpt" }}
        onFindStatus={onFindStatus}
      />,
    );

    await waitFor(() => expect(onFindStatus).toHaveBeenCalledWith("not_found"));
    expect(screen.getByText("第 2 / 3 页")).toBeTruthy();
    expect(screen.getByText("本页未找到完全匹配，已保留目标页。")).toBeTruthy();
    expect(document.querySelector("canvas")?.dataset.renderedPage).toBe("2");
  });
});
