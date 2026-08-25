import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, TextLayer, getDocument } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";


GlobalWorkerOptions.workerSrc = pdfWorkerUrl;


export interface PdfTarget {
  pageIndex: number;
  quote: string;
}

type FindStatus = "idle" | "searching" | "found" | "not_found";
type PdfDocument = Awaited<ReturnType<typeof getDocument>["promise"]>;


interface PdfPaneProps {
  pdfUrl: string | null;
  target: PdfTarget | null;
  onFindStatus?: (status: "found" | "not_found") => void;
}


export function normalizePdfText(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/\u00ad/g, "")
    .replace(/-\s+/g, "")
    .replace(/\s+/g, "")
    .toLocaleLowerCase();
}


function highlightQuote(
  textDivs: HTMLElement[],
  textItems: string[],
  quote: string,
): boolean {
  textDivs.forEach((item) => item.classList.remove("text-highlight"));
  const pieces = textItems.map((item, index) =>
    normalizePdfText(`${item}${index < textItems.length - 1 ? " " : ""}`),
  );
  const pageText = pieces.join("");
  const query = normalizePdfText(quote);
  const matchStart = query ? pageText.indexOf(query) : -1;
  if (matchStart < 0) {
    return false;
  }

  const matchEnd = matchStart + query.length;
  let cursor = 0;
  pieces.forEach((piece, index) => {
    const itemStart = cursor;
    const itemEnd = cursor + piece.length;
    if (itemEnd > matchStart && itemStart < matchEnd) {
      textDivs[index]?.classList.add("text-highlight");
    }
    cursor = itemEnd;
  });
  return true;
}


export function PdfPane({ pdfUrl, target, onFindStatus }: PdfPaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const [pdfDocument, setPdfDocument] = useState<PdfDocument | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1);
  const [loadState, setLoadState] = useState<"empty" | "loading" | "ready" | "failed">(
    "empty",
  );
  const [findStatus, setFindStatus] = useState<FindStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!pdfUrl) {
      setPdfDocument(null);
      setPageCount(0);
      setPageNumber(1);
      setLoadState("empty");
      setErrorMessage(null);
      return;
    }

    let active = true;
    setLoadState("loading");
    setErrorMessage(null);
    const loadingTask = getDocument({
      url: pdfUrl,
      isEvalSupported: false,
      stopAtErrors: false,
    });

    loadingTask.promise
      .then((loadedDocument) => {
        if (!active) {
          return;
        }
        setPdfDocument(loadedDocument);
        setPageCount(loadedDocument.numPages);
        setPageNumber((current) => Math.min(Math.max(current, 1), loadedDocument.numPages));
        setLoadState("ready");
      })
      .catch(() => {
        if (!active) {
          return;
        }
        setPdfDocument(null);
        setLoadState("failed");
        setErrorMessage("PDF 加载失败，请重新读取项目或上传文件。");
      });

    return () => {
      active = false;
      void loadingTask.destroy();
    };
  }, [pdfUrl]);

  useEffect(() => {
    if (!target) {
      setFindStatus("idle");
      return;
    }
    setPageNumber(target.pageIndex + 1);
    setFindStatus("searching");
  }, [target?.pageIndex, target?.quote]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const textLayerContainer = textLayerRef.current;
    if (!pdfDocument || !canvas || !textLayerContainer || pageNumber < 1) {
      return;
    }

    let active = true;
    let renderTask: ReturnType<Awaited<ReturnType<PdfDocument["getPage"]>>["render"]> | null =
      null;
    let textLayer: TextLayer | null = null;

    const renderPage = async () => {
      try {
        const page = await pdfDocument.getPage(pageNumber);
        if (!active) {
          return;
        }
        const viewport = page.getViewport({ scale });
        const outputScale = window.devicePixelRatio || 1;
        const context = canvas.getContext("2d", { alpha: false });
        if (!context) {
          throw new Error("canvas context unavailable");
        }

        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        textLayerContainer.replaceChildren();
        textLayerContainer.style.width = canvas.style.width;
        textLayerContainer.style.height = canvas.style.height;
        textLayerContainer.style.setProperty("--scale-factor", String(scale));

        renderTask = page.render({
          canvas,
          canvasContext: context,
          viewport,
          transform:
            outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
        });
        await renderTask.promise;
        if (!active) {
          return;
        }

        textLayer = new TextLayer({
          textContentSource: page.streamTextContent(),
          container: textLayerContainer,
          viewport,
        });
        await textLayer.render();
        if (!active) {
          return;
        }

        if (target && target.pageIndex + 1 === pageNumber) {
          const found = highlightQuote(
            textLayer.textDivs,
            textLayer.textContentItemsStr,
            target.quote,
          );
          const nextStatus = found ? "found" : "not_found";
          setFindStatus(nextStatus);
          onFindStatus?.(nextStatus);
        } else {
          setFindStatus("idle");
        }
      } catch (error: unknown) {
        if (!active || (error instanceof Error && error.name === "RenderingCancelledException")) {
          return;
        }
        setLoadState("failed");
        setErrorMessage("PDF 页面渲染失败，请重试加载项目。");
      }
    };

    void renderPage();
    return () => {
      active = false;
      renderTask?.cancel();
      textLayer?.cancel();
    };
  }, [onFindStatus, pageNumber, pdfDocument, scale, target?.pageIndex, target?.quote]);

  const changePage = (nextPage: number) => {
    setPageNumber(Math.min(Math.max(nextPage, 1), Math.max(pageCount, 1)));
  };

  return (
    <section className="pane pdf-pane" aria-label="PDF 阅读器">
      <div className="pane-heading pdf-toolbar">
        <div>
          <p className="eyebrow">SOURCE PDF</p>
          <h2>论文原文</h2>
        </div>
        <div className="pdf-controls" aria-label="PDF 控制">
          <button
            type="button"
            aria-label="上一页"
            disabled={pageNumber <= 1 || loadState !== "ready"}
            onClick={() => changePage(pageNumber - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span className="page-indicator">第 {pageNumber} / {pageCount || "—"} 页</span>
          <button
            type="button"
            aria-label="下一页"
            disabled={pageNumber >= pageCount || loadState !== "ready"}
            onClick={() => changePage(pageNumber + 1)}
          >
            <ChevronRight size={16} />
          </button>
          <button
            type="button"
            aria-label="缩小"
            disabled={scale <= 0.7 || loadState !== "ready"}
            onClick={() => setScale((current) => Math.max(0.7, current - 0.15))}
          >
            <ZoomOut size={16} />
          </button>
          <span className="zoom-value">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            aria-label="放大"
            disabled={scale >= 1.75 || loadState !== "ready"}
            onClick={() => setScale((current) => Math.min(1.75, current + 0.15))}
          >
            <ZoomIn size={16} />
          </button>
        </div>
      </div>

      {!pdfUrl ? (
        <div className="pane-empty" role="status">
          <strong>尚未上传 PDF</strong>
          <p>上传并解析后，原文会显示在这里。</p>
        </div>
      ) : null}
      {loadState === "loading" ? <div className="pane-loading">正在加载 PDF…</div> : null}
      {loadState === "failed" ? (
        <div className="pane-error" role="alert">{errorMessage}</div>
      ) : null}
      {pdfUrl ? (
        <div className="pdf-scroll" hidden={loadState === "failed"}>
          <div className="pdf-page">
            <canvas ref={canvasRef} aria-label={`PDF 第 ${pageNumber} 页`} />
            <div ref={textLayerRef} className="text-layer" aria-hidden="true" />
          </div>
        </div>
      ) : null}
      {findStatus === "searching" ? (
        <p className="find-status" aria-live="polite">正在本页查找证据摘录…</p>
      ) : null}
      {findStatus === "found" ? (
        <p className="find-status find-status--found" aria-live="polite">已在本页高亮证据摘录。</p>
      ) : null}
      {findStatus === "not_found" ? (
        <p className="find-status find-status--fallback" aria-live="polite">
          本页未找到完全匹配，已保留目标页。
        </p>
      ) : null}
    </section>
  );
}
