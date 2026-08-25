import type { ContentDraft } from "../types";


interface DocumentPaneProps {
  document: ContentDraft | null;
  selectedSentenceId: string | null;
  evidenceSentenceIds: ReadonlySet<string>;
  onSelectSentence: (sentenceId: string) => void;
}


export function DocumentPane({
  document,
  selectedSentenceId,
  evidenceSentenceIds,
  onSelectSentence,
}: DocumentPaneProps) {
  return (
    <section className="pane document-pane" aria-label="解读文档">
      <div className="pane-heading">
        <div>
          <p className="eyebrow">DOCUMENT</p>
          <h2>解读文档</h2>
        </div>
        <span className="pane-meta">五区结构</span>
      </div>

      {document ? (
        <article className="document-content">
          <h2 className="document-title">{document.title}</h2>
          {document.sections.map((section) => (
            <section className="document-section" key={section.section_id}>
              <h3>{section.heading}</h3>
              <div className="sentence-list">
                {section.sentences.map((sentence) => {
                  const hasEvidence = evidenceSentenceIds.has(sentence.sentence_id);
                  const isSelected = selectedSentenceId === sentence.sentence_id;
                  return (
                    <button
                      className={`sentence${isSelected ? " sentence--selected" : ""}`}
                      type="button"
                      key={sentence.sentence_id}
                      aria-pressed={isSelected}
                      onClick={() => onSelectSentence(sentence.sentence_id)}
                    >
                      <span>{sentence.text}</span>
                      {hasEvidence ? <small>有证据</small> : null}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </article>
      ) : (
        <div className="pane-empty" role="status">
          <strong>尚未生成解读</strong>
          <p>生成完成后，五区解读会显示在这里。</p>
        </div>
      )}
    </section>
  );
}
