// Typed client for the Northstar API, reached through the dev proxy at /api (same-origin cookies).

const BASE = "/api";

export interface Session {
  session_id: string;
  subject_id: string;
  assurance: string;
  tenant_scope: string | null;
  is_admin?: boolean;
}

export interface TermCount {
  term: string;
  count: number;
}

export interface CatalogEntry {
  object_id: string;
  revision_id: string | null;
  title: string;
  summary: string | null;
  document_type: string;
  locale: string;
  terms: Record<string, string[]>;
  published_at?: string | null;
}

export interface Block {
  id: string;
  type: "heading" | "paragraph" | "code" | "quote" | "image" | "list";
  version: number;
  data: { attributes: Record<string, unknown>; content: unknown };
  children: Block[];
}

export interface Revision {
  revision_id: string;
  object_id: string;
  title: string;
  content_hash: string;
  blocks: Block[];
}

export interface SearchHit {
  object_id: string;
  revision_id: string;
  block_id: string;
  text: string;
  score: number;
}

export interface RunResult {
  run_id: string;
  language: string;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  timed_out: boolean;
  truncated: boolean;
  outcome: string;
  record_sha256: string;
  created_at: string;
  lesson_id: string | null;
  block_id: string | null;
}

export interface Comment {
  annotation_id: string;
  thread_id: string | null;
  parent_annotation_id: string | null;
  body_type: string;
  body_content: unknown;
  creator_id: string;
  creator_type: string;
  created_at: string;
  state: string;
  visibility: string;
  motivation: string;
}

export interface AccountEvent {
  event_type: string;
  created_at: string;
  detail: string | null;
  subject_id?: string;
}

export interface EmailTemplateSummary {
  template_id: string;
  version: number;
  subject: string;
  required_variables: string[];
}

export interface EmailTemplate extends EmailTemplateSummary {
  html_body: string;
  text_body: string;
}

export interface OutboxMessage {
  message_id: string;
  to_email: string;
  template_id: string | null;
  subject: string;
  html_body: string;
  status: string;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    // Never serve stale API data from the HTTP cache (content changes as the DB is curated).
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.title ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  loginUrl(): string {
    return `${BASE}/auth/login`;
  },
  async session(): Promise<Session | null> {
    try {
      return await req<Session>("/auth/session");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null;
      throw e;
    }
  },
  async logout(): Promise<void> {
    // Best-effort: reads the readable CSRF cookie for the double-submit check.
    const csrf = document.cookie
      .split("; ")
      .find((c) => c.startsWith("ns_csrf="))
      ?.split("=")[1];
    await req<unknown>("/auth/logout", {
      method: "POST",
      headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
    }).catch(() => undefined);
  },

  // ---- Local (email + password) auth ----
  register(email: string, password: string): Promise<{ email: string; confirmation_required: boolean }> {
    return req("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) });
  },
  login(email: string, password: string): Promise<{ subject_id: string }> {
    return req("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  },
  confirmEmail(token: string): Promise<{ confirmed: boolean; email: string }> {
    return req("/auth/confirm", { method: "POST", body: JSON.stringify({ token }) });
  },
  forgotPassword(email: string): Promise<{ ok: boolean }> {
    return req("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) });
  },
  resetPassword(token: string, password: string): Promise<{ ok: boolean }> {
    return req("/auth/reset-password", { method: "POST", body: JSON.stringify({ token, password }) });
  },
  resendConfirmation(email: string): Promise<{ ok: boolean }> {
    return req("/auth/resend-confirmation", { method: "POST", body: JSON.stringify({ email }) });
  },
  myActivity(limit = 50): Promise<{ events: AccountEvent[] }> {
    return req(`/auth/activity?limit=${limit}`);
  },
  adminActivity(params: {
    limit?: number;
    offset?: number;
    event_type?: string;
    q?: string;
    created_after?: string;
    created_before?: string;
  } = {}): Promise<{ events: AccountEvent[]; total: number }> {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.event_type) q.set("event_type", params.event_type);
    if (params.q) q.set("q", params.q);
    if (params.created_after) q.set("created_after", params.created_after);
    if (params.created_before) q.set("created_before", params.created_before);
    return req(`/auth/admin/activity?${q.toString()}`);
  },
  adminStats(): Promise<{
    documents: number;
    topics: number;
    courses: number;
    emails: number;
    code_runs: number;
    users: number;
    confirmed_users: number;
    recent: Array<{ event_type: string; detail: string | null; created_at: string }>;
  }> {
    return req("/admin/stats");
  },

  // ---- Email templates + outbox (admin) ----
  emailTemplates(): Promise<{ templates: EmailTemplateSummary[] }> {
    return req("/messaging/templates");
  },
  emailTemplate(templateId: string): Promise<EmailTemplate> {
    return req(`/messaging/templates/${encodeURIComponent(templateId)}`);
  },
  publishEmailTemplate(input: {
    template_id: string;
    version: number;
    subject: string;
    html_body: string;
    text_body: string;
    required_variables: string[];
  }): Promise<{ template_id: string; version: number }> {
    const csrf = document.cookie
      .split("; ")
      .find((c) => c.startsWith("ns_csrf="))
      ?.split("=")[1];
    return req("/messaging/templates/publish", {
      method: "POST",
      headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
      body: JSON.stringify(input),
    });
  },
  outbox(params: {
    limit?: number;
    offset?: number;
    status?: string;
    q?: string;
    created_after?: string;
    created_before?: string;
  } = {}): Promise<{ messages: OutboxMessage[]; total: number }> {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.status) q.set("status", params.status);
    if (params.q) q.set("q", params.q);
    if (params.created_after) q.set("created_after", params.created_after);
    if (params.created_before) q.set("created_before", params.created_before);
    return req(`/messaging/outbox?${q.toString()}`);
  },
  taxonomy(scheme: string): Promise<{ scheme: string; terms: TermCount[] }> {
    return req(`/knowledge/taxonomy/${encodeURIComponent(scheme)}`);
  },
  categories(): Promise<{ scheme: string; terms: TermCount[] }> {
    return this.taxonomy("category");
  },
  // Human-readable category titles, keyed by category code (from each category's overview doc).
  async categoryLabels(): Promise<Record<string, string>> {
    const { entries } = await this.catalog({ kind: "overview", limit: 1000 });
    const map: Record<string, string> = {};
    for (const e of entries) {
      const cat = e.terms.category?.[0];
      const hasModule = (e.terms.module?.length ?? 0) > 0;
      if (cat && !hasModule && !map[cat]) {
        map[cat] = e.title.replace(/^C\d+\s*[—–-]\s*/, "").trim();
      }
    }
    return map;
  },
  catalog(params: {
    category?: string;
    module?: string;
    kind?: string;
    subject?: string;
    phase?: string;
    phase_title?: string;
    q?: string;
    published_after?: string;
    published_before?: string;
    sort?: "order" | "recent";
    include_total?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<{ entries: CatalogEntry[]; total?: number }> {
    const q = new URLSearchParams();
    if (params.category) q.set("category", params.category);
    if (params.module) q.set("module", params.module);
    if (params.kind) q.set("kind", params.kind);
    if (params.subject) q.set("subject", params.subject);
    if (params.phase) q.set("phase", params.phase);
    if (params.phase_title) q.set("phase_title", params.phase_title);
    if (params.q) q.set("q", params.q);
    if (params.published_after) q.set("published_after", params.published_after);
    if (params.published_before) q.set("published_before", params.published_before);
    if (params.sort) q.set("sort", params.sort);
    if (params.include_total) q.set("include_total", "1");
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return req(`/knowledge/catalog?${q.toString()}`);
  },
  revision(revisionId: string): Promise<Revision> {
    return req(`/knowledge/revisions/${encodeURIComponent(revisionId)}`);
  },
  // Compact global map (lesson id -> {r: revisionId, t: title}) for cross-reference hyperlinks.
  lessonIndex(): Promise<{ lessons: Record<string, { r: string; t: string }> }> {
    return req("/knowledge/lesson-index");
  },
  search(text: string, topK = 15): Promise<{ query: string; results: SearchHit[] }> {
    return req("/retrieval/search", {
      method: "POST",
      body: JSON.stringify({ q: text, top_k: topK }),
    });
  },
  runCode(input: {
    code: string;
    language?: string;
    lesson_id?: string | null;
    block_id?: string | null;
    stdin?: string;
  }): Promise<RunResult> {
    return req("/codelab/runs", { method: "POST", body: JSON.stringify(input) });
  },
  myRuns(limit = 50): Promise<{ runs: RunResult[] }> {
    return req(`/codelab/runs?limit=${limit}`);
  },
  comments(objectId: string): Promise<{ annotations: Comment[] }> {
    return req(`/annotations/target/${encodeURIComponent(objectId)}`);
  },
  postComment(input: {
    object_id: string;
    revision_id: string;
    block_id: string;
    body: string;
  }): Promise<{ annotation_id: string; thread_id: string; state: string }> {
    return req("/annotations", {
      method: "POST",
      body: JSON.stringify({
        object_id: input.object_id,
        revision_id: input.revision_id,
        selectors: [{ type: "BlockSelector", block_id: input.block_id }],
        motivation: "commenting",
        visibility: "public",
        body_type: "text",
        body_content: input.body,
      }),
    });
  },
  replyComment(
    parentId: string,
    body: string,
  ): Promise<{ annotation_id: string; thread_id: string; parent_annotation_id: string }> {
    return req(`/annotations/${encodeURIComponent(parentId)}/replies`, {
      method: "POST",
      body: JSON.stringify({
        motivation: "commenting",
        visibility: "public",
        body_type: "text",
        body_content: body,
      }),
    });
  },

  // ---- content management (CMS) ----
  getDocument(objectId: string): Promise<{
    object_id: string;
    document_type: string;
    locale: string;
    lifecycle: string;
    latest_revision_id: string | null;
  }> {
    return req(`/knowledge/${encodeURIComponent(objectId)}`);
  },
  createDocument(input: {
    document_type: string;
    locale: string;
    title: string;
    blocks: Block[];
    summary?: string;
  }): Promise<{ object_id: string; draft_id: string }> {
    return req("/knowledge", { method: "POST", body: JSON.stringify(input) });
  },
  editDraft(objectId: string, blocks: Block[]): Promise<{ object_id: string; version: number }> {
    return req(`/knowledge/${encodeURIComponent(objectId)}/draft`, {
      method: "POST",
      body: JSON.stringify({ blocks }),
    });
  },
  submitDocument(objectId: string): Promise<{ object_id: string; lifecycle: string }> {
    return req(`/knowledge/${encodeURIComponent(objectId)}/submit`, { method: "POST" });
  },
  publishDocument(
    objectId: string,
    input: { title: string; visibility?: string; summary?: string },
  ): Promise<{ object_id: string; revision_id: string }> {
    return req(`/knowledge/${encodeURIComponent(objectId)}/publish`, {
      method: "POST",
      body: JSON.stringify({ visibility: "organization", ...input }),
    });
  },
  assignTaxonomy(
    objectId: string,
    scheme: string,
    term: string,
  ): Promise<{ object_id: string; scheme: string; term: string }> {
    return req(`/knowledge/${encodeURIComponent(objectId)}/taxonomy`, {
      method: "POST",
      body: JSON.stringify({ scheme, term }),
    });
  },
  moderateComment(
    annotationId: string,
    kind: string,
    reason?: string,
  ): Promise<{ annotation_id: string; state: string }> {
    return req(`/annotations/${encodeURIComponent(annotationId)}/moderation`, {
      method: "POST",
      body: JSON.stringify({ kind, reason }),
    });
  },

  // ---- AI assistant ----
  assistantModels(): Promise<{
    active: string;
    models: Array<{ id: string; label: string; kind: string; active: boolean }>;
  }> {
    return req("/assistant/models");
  },
  setAssistantModel(modelId: string): Promise<{ active: string }> {
    return req("/assistant/config", { method: "POST", body: JSON.stringify({ model_id: modelId }) });
  },
  ask(input: {
    question: string;
    lesson_object_id?: string | null;
    model_id?: string | null;
  }): Promise<{
    answer: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    sources: Array<{ object_id: string; revision_id: string; block_id: string; snippet: string }>;
  }> {
    return req("/assistant/ask", { method: "POST", body: JSON.stringify(input) });
  },
};
