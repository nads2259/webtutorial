import type { Block } from "../../api/client";

const TYPES: Array<Block["type"]> = ["heading", "paragraph", "code", "quote", "list", "image"];

function newId(): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "")
      : `${Date.now()}${Math.random().toString(36).slice(2)}`;
  return `blk-${rand.slice(0, 24)}`;
}

export function emptyBlock(type: Block["type"]): Block {
  const base = { id: newId(), version: 1, children: [] as Block[] };
  switch (type) {
    case "heading":
      return { ...base, type, data: { attributes: { level: 2 }, content: "New heading" } };
    case "code":
      return { ...base, type, data: { attributes: { language: "python" }, content: "" } };
    case "list":
      return { ...base, type, data: { attributes: { ordered: false }, content: ["Item one"] } };
    case "image":
      return { ...base, type, data: { attributes: { alt: "" }, content: "" } };
    case "quote":
      return { ...base, type, data: { attributes: {}, content: "" } };
    default:
      return { ...base, type: "paragraph", data: { attributes: {}, content: "New paragraph." } };
  }
}

export interface BlockEditorProps {
  blocks: Block[];
  onChange: (blocks: Block[]) => void;
}

/** A structured editor over the canonical knowledge block model (used by the CMS). */
export function BlockEditor({ blocks, onChange }: BlockEditorProps): React.JSX.Element {
  function update(i: number, next: Block): void {
    const copy = blocks.slice();
    copy[i] = next;
    onChange(copy);
  }
  function remove(i: number): void {
    onChange(blocks.filter((_, idx) => idx !== i));
  }
  function move(i: number, dir: -1 | 1): void {
    const j = i + dir;
    if (j < 0 || j >= blocks.length) return;
    const copy = blocks.slice();
    [copy[i], copy[j]] = [copy[j], copy[i]];
    onChange(copy);
  }
  function add(type: Block["type"]): void {
    onChange([...blocks, emptyBlock(type)]);
  }

  return (
    <div className="bip-blocks">
      {blocks.map((block, i) => (
        <div key={block.id} className="bip-block">
          <div className="bip-block__bar">
            <span className="bip-block__type">{block.type}</span>
            <div className="bip-block__ctrls">
              <button type="button" onClick={() => move(i, -1)} aria-label="Move up" disabled={i === 0}>
                ↑
              </button>
              <button
                type="button"
                onClick={() => move(i, 1)}
                aria-label="Move down"
                disabled={i === blocks.length - 1}
              >
                ↓
              </button>
              <button type="button" onClick={() => remove(i)} aria-label="Delete block">
                ✕
              </button>
            </div>
          </div>
          <BlockFields block={block} onChange={(b) => update(i, b)} />
        </div>
      ))}
      <div className="bip-blocks__add">
        <span>Add block:</span>
        {TYPES.map((t) => (
          <button key={t} type="button" onClick={() => add(t)}>
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

function BlockFields({
  block,
  onChange,
}: {
  block: Block;
  onChange: (b: Block) => void;
}): React.JSX.Element {
  const attrs = block.data.attributes;
  const setContent = (content: unknown): void =>
    onChange({ ...block, data: { ...block.data, content } });
  const setAttr = (key: string, value: unknown): void =>
    onChange({ ...block, data: { ...block.data, attributes: { ...attrs, [key]: value } } });

  switch (block.type) {
    case "heading":
      return (
        <div className="bip-block__fields">
          <label>
            Level
            <select
              value={Number(attrs.level ?? 2)}
              onChange={(e) => setAttr("level", Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  H{n}
                </option>
              ))}
            </select>
          </label>
          <input
            value={String(block.data.content ?? "")}
            onChange={(e) => setContent(e.target.value)}
            aria-label="Heading text"
          />
        </div>
      );
    case "code":
      return (
        <div className="bip-block__fields">
          <input
            value={String(attrs.language ?? "text")}
            onChange={(e) => setAttr("language", e.target.value)}
            aria-label="Language"
            placeholder="language (python, mermaid, dot…)"
          />
          <textarea
            className="bip-mono"
            value={String(block.data.content ?? "")}
            onChange={(e) => setContent(e.target.value)}
            rows={6}
            aria-label="Code"
          />
        </div>
      );
    case "list": {
      const items = Array.isArray(block.data.content) ? (block.data.content as unknown[]) : [];
      return (
        <div className="bip-block__fields">
          <label className="bip-inline">
            <input
              type="checkbox"
              checked={Boolean(attrs.ordered)}
              onChange={(e) => setAttr("ordered", e.target.checked)}
            />
            Ordered
          </label>
          <textarea
            value={items.map((x) => String(x)).join("\n")}
            onChange={(e) => setContent(e.target.value.split("\n").filter((l) => l.trim()))}
            rows={4}
            aria-label="List items (one per line)"
            placeholder="One item per line"
          />
        </div>
      );
    }
    case "image":
      return (
        <div className="bip-block__fields">
          <input
            value={String(block.data.content ?? "")}
            onChange={(e) => setContent(e.target.value)}
            aria-label="Image or video URL"
            placeholder="https://… (image or .mp4/.webm)"
          />
          <input
            value={String(attrs.alt ?? "")}
            onChange={(e) => setAttr("alt", e.target.value)}
            aria-label="Alt text / caption"
            placeholder="Alt text / caption"
          />
        </div>
      );
    default:
      return (
        <div className="bip-block__fields">
          <textarea
            value={String(block.data.content ?? "")}
            onChange={(e) => setContent(e.target.value)}
            rows={3}
            aria-label={block.type}
          />
        </div>
      );
  }
}
