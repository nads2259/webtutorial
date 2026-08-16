import { color, size, space, type as typeTokens } from "@northstar/design-tokens";
import { Button, Status } from "@northstar/ui-primitives";
import { baseKeymap } from "prosemirror-commands";
import { history, redo, undo } from "prosemirror-history";
import { keymap } from "prosemirror-keymap";
import { type Command, EditorState } from "prosemirror-state";
import { EditorView } from "prosemirror-view";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import type { Block, BlockType } from "./blocks";
import { deleteCurrentBlock, insertBlockAfter, moveBlock } from "./commands";
import { docToTree, treeToDoc } from "./convert";

export interface StructuredEditorProps {
  /** Initial typed block tree; captured once when the editor mounts. */
  initialBlocks: readonly Block[];
  /** Fires with the current typed block tree after every content change. */
  onChange?: (blocks: Block[]) => void;
  /** Accessible name for the editable region (WCAG 4.1.2). */
  label?: string;
}

const INSERTABLE: readonly { kind: BlockType; label: string }[] = [
  { kind: "paragraph", label: "Paragraph" },
  { kind: "heading", label: "Heading" },
  { kind: "code", label: "Code" },
  { kind: "quote", label: "Quote" },
  { kind: "list", label: "List" },
  { kind: "image", label: "Image" },
];

const toolbarStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: space.sm,
  marginBottom: space.md,
};

const groupStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: space.xs,
  alignItems: "center",
};

const buttonStyle: CSSProperties = {
  minInlineSize: size.minTargetPx,
  minBlockSize: size.minTargetPx,
  padding: `${space.xs}px ${space.sm}px`,
  border: `1px solid ${color.border}`,
  borderRadius: 4,
  background: color.surface,
  color: color.text,
  cursor: "pointer",
};

const surfaceStyle: CSSProperties = {
  border: `1px solid ${color.border}`,
  borderRadius: 4,
  padding: space.md,
  minBlockSize: 160,
  background: color.surface,
  color: color.text,
  fontFamily: typeTokens.fontFamily,
  fontSize: typeTokens.baseSizePx,
  lineHeight: typeTokens.lineHeight,
};

/**
 * Structured block editor over the canonical typed block tree. Editing happens
 * on a ProseMirror document whose schema maps 1:1 to the typed blocks; block
 * add/move/delete are keyboard-operable buttons (and Alt+Arrow shortcuts),
 * providing the required drag alternative (WCAG 2.5.7) and full keyboard
 * operability (2.1.1). No marks are defined, so no executable/styled markup can
 * be authored or pasted (LAW-08).
 */
export function StructuredEditor({
  initialBlocks,
  onChange,
  label = "Document body",
}: StructuredEditorProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const initialRef = useRef(initialBlocks);
  const labelRef = useRef(label);
  labelRef.current = label;
  const [statusMessage, setStatusMessage] = useState("");

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) {
      return;
    }
    const state = EditorState.create({
      doc: treeToDoc(initialRef.current),
      plugins: [
        history(),
        keymap({
          "Mod-z": undo,
          "Mod-y": redo,
          "Shift-Mod-z": redo,
          "Alt-ArrowUp": moveBlock(-1),
          "Alt-ArrowDown": moveBlock(1),
        }),
        keymap(baseKeymap),
      ],
    });
    const view = new EditorView(mount, {
      state,
      attributes: {
        "aria-label": labelRef.current,
        "aria-multiline": "true",
        role: "textbox",
      },
      dispatchTransaction(transaction) {
        const next = view.state.apply(transaction);
        view.updateState(next);
        if (transaction.docChanged) {
          onChangeRef.current?.(docToTree(next.doc));
        }
      },
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  function runCommand(command: Command, message: string): void {
    const view = viewRef.current;
    if (!view) {
      return;
    }
    const handled = command(view.state, view.dispatch, view);
    view.focus();
    setStatusMessage(handled ? message : `${message} — not available here`);
  }

  return (
    <section aria-label="Structured content editor">
      <div role="toolbar" aria-label="Block operations" style={toolbarStyle}>
        <div style={groupStyle}>
          {INSERTABLE.map(({ kind, label: kindLabel }) => (
            <Button
              key={kind}
              style={buttonStyle}
              onClick={() => runCommand(insertBlockAfter(kind), `Inserted ${kindLabel} block`)}
            >
              Add {kindLabel}
            </Button>
          ))}
        </div>
        <div style={groupStyle}>
          <Button style={buttonStyle} onClick={() => runCommand(moveBlock(-1), "Moved block up")}>
            Move up
          </Button>
          <Button style={buttonStyle} onClick={() => runCommand(moveBlock(1), "Moved block down")}>
            Move down
          </Button>
          <Button
            style={buttonStyle}
            onClick={() => runCommand(deleteCurrentBlock(), "Deleted block")}
          >
            Delete block
          </Button>
        </div>
      </div>
      <div ref={mountRef} style={surfaceStyle} data-testid="editor-surface" />
      <Status>{statusMessage}</Status>
    </section>
  );
}
