import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

/** Render assistant markdown safely as React nodes (fenced code, headings, bold, inline code). */
function renderAnswer(text: string): React.JSX.Element {
  const segments = text.split(/```/);
  return (
    <>
      {segments.map((seg, i) => {
        if (i % 2 === 1) {
          const code = seg.replace(/^[a-zA-Z0-9]*\n/, "");
          return (
            <pre key={i} className="bip-turn__code">
              <code>{code.replace(/\n$/, "")}</code>
            </pre>
          );
        }
        return (
          <div key={i}>
            {seg.split("\n").map((line, j) => {
              const heading = /^#{1,6}\s+(.*)$/.exec(line);
              const content = heading ? heading[1] : line;
              if (!content.trim()) return <br key={j} />;
              const parts = content.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
              const nodes = parts.map((p, k) => {
                if (p.startsWith("**") && p.endsWith("**")) return <strong key={k}>{p.slice(2, -2)}</strong>;
                if (p.startsWith("`") && p.endsWith("`")) return <code key={k}>{p.slice(1, -1)}</code>;
                return <span key={k}>{p}</span>;
              });
              return heading ? (
                <p key={j} className="bip-turn__h">
                  {nodes}
                </p>
              ) : (
                <p key={j} className="bip-turn__p">
                  {nodes}
                </p>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  model?: string;
  sources?: Array<{ object_id: string; revision_id: string; block_id: string; snippet: string }>;
}

export interface AssistantPanelProps {
  /** The current lesson's document id, for context (when the learner is reading a lesson). */
  lessonObjectId: string | null;
}

/**
 * A right-side AI assistant available to everyone (including anonymous visitors). It answers questions
 * grounded on the indexed curriculum (via the `assistant.ask` capability) using the admin-configured
 * model. Toggled by a floating button.
 */
export function AssistantPanel({ lessonObjectId }: AssistantPanelProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<Array<{ id: string; label: string }>>([]);
  const [activeModel, setActiveModel] = useState<string>("");
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || models.length) return;
    api
      .assistantModels()
      .then((r) => {
        setModels(r.models.map((m) => ({ id: m.id, label: m.label })));
        setActiveModel(r.active);
      })
      .catch(() => undefined);
  }, [open, models.length]);

  useEffect(() => {
    const el = bodyRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [turns, busy]);

  async function ask(): Promise<void> {
    const q = question.trim();
    if (!q) return;
    setTurns((t) => [...t, { role: "user", text: q }]);
    setQuestion("");
    setBusy(true);
    try {
      const r = await api.ask({
        question: q,
        lesson_object_id: lessonObjectId,
        model_id: activeModel || undefined,
      });
      setTurns((t) => [
        ...t,
        { role: "assistant", text: r.answer, model: r.model, sources: r.sources },
      ]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "The assistant is unavailable right now.";
      setTurns((t) => [...t, { role: "assistant", text: `⚠️ ${msg}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="bip-assistant__fab"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="assistant-panel"
      >
        {open ? "✕" : "✦ Ask AI"}
      </button>

      <aside
        id="assistant-panel"
        className={`bip-assistant${open ? " bip-assistant--open" : ""}`}
        aria-label="AI assistant"
        aria-hidden={!open}
      >
        <div className="bip-assistant__head">
          <div>
            <strong>Ask the AI tutor</strong>
            <p>Grounded on the course. Answers can be imperfect — verify code before relying on it.</p>
          </div>
          <button type="button" onClick={() => setOpen(false)} aria-label="Close assistant">
            ✕
          </button>
        </div>

        {models.length > 1 ? (
          <div className="bip-assistant__model">
            <label>
              Model
              <select value={activeModel} onChange={(e) => setActiveModel(e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}

        <div className="bip-assistant__body" ref={bodyRef}>
          {turns.length === 0 ? (
            <p className="bip-assistant__hint">
              Ask anything about Python or this lesson — e.g. “Explain decorators with a small
              example”.
            </p>
          ) : (
            turns.map((t, i) => (
              <div key={i} className={`bip-turn bip-turn--${t.role}`}>
                <div className="bip-turn__text">
                  {t.role === "assistant" ? renderAnswer(t.text) : t.text}
                </div>
                {t.sources && t.sources.length > 0 ? (
                  <div className="bip-turn__sources">
                    <span>Sources:</span>
                    {t.sources.slice(0, 4).map((s) => (
                      <a key={s.block_id} href={`/l/_/${s.revision_id}`} title={s.snippet}>
                        lesson
                      </a>
                    ))}
                  </div>
                ) : null}
              </div>
            ))
          )}
          {busy ? <div className="bip-turn bip-turn--assistant">Thinking…</div> : null}
        </div>

        <form
          className="bip-assistant__form"
          onSubmit={(e) => {
            e.preventDefault();
            ask();
          }}
        >
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                ask();
              }
            }}
            placeholder="Ask a question…"
            rows={2}
            aria-label="Your question"
          />
          <button type="submit" disabled={busy || !question.trim()}>
            Send
          </button>
        </form>
      </aside>
    </>
  );
}
