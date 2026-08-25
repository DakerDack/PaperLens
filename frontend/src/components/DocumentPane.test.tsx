import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ContentDraft } from "../types";
import { DocumentPane } from "./DocumentPane";


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


afterEach(cleanup);


describe("DocumentPane", () => {
  it("renders all five sections and reports the selected sentence id", () => {
    const onSelectSentence = vi.fn();
    render(
      <DocumentPane
        document={document}
        selectedSentenceId={null}
        evidenceSentenceIds={new Set(["s-3"])}
        onSelectSentence={onSelectSentence}
      />,
    );

    expect(screen.getAllByRole("heading", { level: 3 }).map((item) => item.textContent)).toEqual([
      "研究问题",
      "研究方法",
      "主要结果",
      "研究局限",
      "通俗解释",
    ]);

    fireEvent.click(screen.getByRole("button", { name: /实验组表现更好/ }));
    expect(onSelectSentence).toHaveBeenCalledWith("s-3");
    expect(screen.getByText("有证据")).toBeTruthy();
  });

  it("keeps the empty document state explicit", () => {
    render(
      <DocumentPane
        document={null}
        selectedSentenceId={null}
        evidenceSentenceIds={new Set()}
        onSelectSentence={vi.fn()}
      />,
    );

    expect(screen.getByText("生成完成后，五区解读会显示在这里。")).toBeTruthy();
  });
});
