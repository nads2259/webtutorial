import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type Comment } from "../api/client";

export interface CommentsPanelProps {
  objectId: string;
  revisionId: string;
  anchorBlockId: string | null;
  authenticated: boolean;
  subjectId: string | null;
}

function authorLabel(c: Comment, subjectId: string | null): string {
  if (subjectId && c.creator_id === subjectId) return "You";
  return `Learner ${c.creator_id.slice(0, 6)}`;
}

function initials(c: Comment, subjectId: string | null): string {
  if (subjectId && c.creator_id === subjectId) return "You";
  return c.creator_id.slice(0, 2).toUpperCase();
}

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * Per-lesson discussion, backed by the annotation module. Learners post document-level comments
 * (anchored to the lesson's first block) and reply in threads. Public visibility, so everyone in the
 * tenant sees the conversation.
 */
export function CommentsPanel({
  objectId,
  revisionId,
  anchorBlockId,
  authenticated,
  subjectId,
}: CommentsPanelProps): React.JSX.Element {
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [replyBody, setReplyBody] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .comments(objectId)
      .then((r) => setComments(r.annotations.filter((a) => a.state !== "deleted")))
      .catch(() => setComments([]))
      .finally(() => setLoading(false));
  }, [objectId]);

  useEffect(() => {
    load();
  }, [load]);

  const topLevel = useMemo(
    () =>
      comments
        .filter((c) => !c.parent_annotation_id)
        .sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [comments],
  );
  const repliesOf = useCallback(
    (id: string) =>
      comments
        .filter((c) => c.parent_annotation_id === id)
        .sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [comments],
  );

  async function submit(): Promise<void> {
    if (!body.trim() || !anchorBlockId) return;
    setBusy(true);
    setError(null);
    try {
      await api.postComment({
        object_id: objectId,
        revision_id: revisionId,
        block_id: anchorBlockId,
        body: body.trim(),
      });
      setBody("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to post comment");
    } finally {
      setBusy(false);
    }
  }

  async function submitReply(parentId: string): Promise<void> {
    if (!replyBody.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.replyComment(parentId, replyBody.trim());
      setReplyBody("");
      setReplyTo(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to reply");
    } finally {
      setBusy(false);
    }
  }

  function renderComment(c: Comment, isReply = false): React.JSX.Element {
    return (
      <li key={c.annotation_id} className={`bip-comment${isReply ? " bip-comment--reply" : ""}`}>
        <div className="bip-comment__avatar" aria-hidden="true">
          {initials(c, subjectId)}
        </div>
        <div className="bip-comment__main">
          <div className="bip-comment__head">
            <span className="bip-comment__author">{authorLabel(c, subjectId)}</span>
            <span className="bip-comment__time">{when(c.created_at)}</span>
          </div>
          <p className="bip-comment__body">{String(c.body_content ?? "")}</p>
          {!isReply && authenticated ? (
            <div className="bip-comment__actions">
              <button
                type="button"
                className="bip-comment__replybtn"
                onClick={() => setReplyTo(replyTo === c.annotation_id ? null : c.annotation_id)}
              >
                {replyTo === c.annotation_id ? "Cancel" : "Reply"}
              </button>
            </div>
          ) : null}
          {replyTo === c.annotation_id ? (
            <div className="bip-comment__replyform">
              <textarea
                className="bip-comment__input"
                value={replyBody}
                onChange={(e) => setReplyBody(e.target.value)}
                placeholder="Write a reply…"
                rows={2}
                aria-label="Reply"
              />
              <button
                type="button"
                className="bip-comment__submit"
                disabled={busy || !replyBody.trim()}
                onClick={() => submitReply(c.annotation_id)}
              >
                Reply
              </button>
            </div>
          ) : null}
          {!isReply ? (
            <ul className="bip-comment__replies">
              {repliesOf(c.annotation_id).map((r) => renderComment(r, true))}
            </ul>
          ) : null}
        </div>
      </li>
    );
  }

  return (
    <section className="bip-comments" aria-labelledby="comments-title">
      <h2 id="comments-title">
        Discussion{topLevel.length > 0 ? ` (${comments.length})` : ""}
      </h2>

      {authenticated ? (
        <div className="bip-comments__new">
          <textarea
            className="bip-comment__input"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Share a question or insight about this lesson…"
            rows={3}
            aria-label="Add a comment"
          />
          <div className="bip-comments__newbar">
            {error ? <span className="bip-comment__error">{error}</span> : <span />}
            <button
              type="button"
              className="bip-comment__submit"
              disabled={busy || !body.trim() || !anchorBlockId}
              onClick={submit}
            >
              {busy ? "Posting…" : "Post comment"}
            </button>
          </div>
        </div>
      ) : (
        <p className="bip-comments__signin">
          <a href={api.loginUrl()}>Sign in</a> to join the discussion.
        </p>
      )}

      {loading ? (
        <p>Loading comments…</p>
      ) : topLevel.length === 0 ? (
        <p className="bip-comments__empty">No comments yet. Be the first to start the discussion.</p>
      ) : (
        <ul className="bip-comments__list">{topLevel.map((c) => renderComment(c))}</ul>
      )}
    </section>
  );
}
