import type { Node as PMNode } from "prosemirror-model";
import { type Command, type EditorState, Selection } from "prosemirror-state";
import { type BlockType, newBlockId } from "./blocks";
import { editorSchema } from "./schema";

/**
 * Keyboard-operable block operations (add / move / delete) exposed as
 * ProseMirror commands. They provide the single-pointer / keyboard alternative
 * to drag reordering (WCAG 2.2 AA 2.5.7) and full keyboard operability (2.1.1).
 * New blocks receive a freshly generated stable id; existing ids are untouched.
 */

function topLevelNodes(doc: PMNode): PMNode[] {
  const nodes: PMNode[] = [];
  for (let i = 0; i < doc.childCount; i += 1) {
    nodes.push(doc.child(i));
  }
  return nodes;
}

function currentIndex(state: EditorState): number {
  return state.selection.$from.index(0);
}

function posBeforeIndex(nodes: readonly PMNode[], index: number): number {
  let pos = 0;
  for (let i = 0; i < index; i += 1) {
    const node = nodes[i];
    if (node) {
      pos += node.nodeSize;
    }
  }
  return pos;
}

function emptyBlockNode(kind: BlockType): PMNode {
  const blockId = newBlockId();
  switch (kind) {
    case "heading":
      return editorSchema.node("heading", { blockId, version: 1, level: 2 });
    case "paragraph":
      return editorSchema.node("paragraph", { blockId, version: 1 });
    case "code":
      return editorSchema.node("code_block", { blockId, version: 1, language: null });
    case "quote":
      return editorSchema.node("quote", { blockId, version: 1 });
    case "image":
      return editorSchema.node("image", { blockId, version: 1, src: "", alt: "" });
    case "list":
      return editorSchema.node("list", { blockId, version: 1, ordered: false }, [
        editorSchema.node("list_item"),
      ]);
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

/** Inserts a new empty block of `kind` immediately after the current block. */
export function insertBlockAfter(kind: BlockType): Command {
  return (state, dispatch) => {
    const nodes = topLevelNodes(state.doc);
    const index = currentIndex(state);
    const pos = posBeforeIndex(nodes, index + 1);
    if (dispatch) {
      const tr = state.tr.insert(pos, emptyBlockNode(kind));
      const selection = Selection.near(tr.doc.resolve(pos + 1));
      dispatch(tr.setSelection(selection).scrollIntoView());
    }
    return true;
  };
}

/** Moves the current block one slot toward the start (-1) or end (+1). */
export function moveBlock(direction: -1 | 1): Command {
  return (state, dispatch) => {
    const nodes = topLevelNodes(state.doc);
    const index = currentIndex(state);
    const target = index + direction;
    if (target < 0 || target >= nodes.length) {
      return false;
    }
    if (dispatch) {
      const reordered = nodes.slice();
      const [moved] = reordered.splice(index, 1);
      if (moved) {
        reordered.splice(target, 0, moved);
      }
      const tr = state.tr.replaceWith(0, state.doc.content.size, reordered);
      const pos = posBeforeIndex(reordered, target);
      const selection = Selection.near(tr.doc.resolve(pos + 1));
      dispatch(tr.setSelection(selection).scrollIntoView());
    }
    return true;
  };
}

/** Deletes the current block, keeping at least one (empty paragraph) block. */
export function deleteCurrentBlock(): Command {
  return (state, dispatch) => {
    const nodes = topLevelNodes(state.doc);
    const index = currentIndex(state);
    const node = nodes[index];
    if (!node) {
      return false;
    }
    if (dispatch) {
      if (nodes.length === 1) {
        const tr = state.tr.replaceWith(0, state.doc.content.size, emptyBlockNode("paragraph"));
        dispatch(tr.setSelection(Selection.near(tr.doc.resolve(1))).scrollIntoView());
        return true;
      }
      const from = posBeforeIndex(nodes, index);
      const tr = state.tr.delete(from, from + node.nodeSize);
      const anchor = Math.min(from, tr.doc.content.size);
      dispatch(tr.setSelection(Selection.near(tr.doc.resolve(anchor))).scrollIntoView());
    }
    return true;
  };
}
