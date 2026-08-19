import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Block, type CatalogEntry, type Session, type TermCount } from "../../api/client";
import { cleanLessonTitle, kindLabel, subjectLabel } from "../../lib/tree";
import { BlockEditor, emptyBlock } from "./BlockEditor";

const PAGE_SIZE = 25;

const ACTIVITY_TYPES = [
  "registered",
  "email_confirmed",
  "login",
  "login_failed",
  "password_reset_requested",
  "password_reset",
  "confirmation_resent",
] as const;

const OUTBOX_STATUSES = ["recorded", "sent", "failed"] as const;

function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString();
}

function compareTerms(a: string, b: string): number {
  const parse = (value: string) => {
    const match = value.match(/^([A-Za-z-]+)(\d+)$/);
    return match
      ? { prefix: match[1] ?? value, n: Number(match[2]), raw: value }
      : { prefix: value, n: 0, raw: value };
  };
  const left = parse(a);
  const right = parse(b);
  if (left.prefix !== right.prefix) return left.prefix.localeCompare(right.prefix);
  if (left.n !== right.n) return left.n - right.n;
  return left.raw.localeCompare(right.raw);
}

function AdminPager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}): React.JSX.Element {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);
  return (
    <div className="bip-admin__pager">
      <button type="button" onClick={() => onPage(Math.max(0, page - 1))} disabled={page === 0}>
        ← Previous
      </button>
      <span className="bip-admin__pageinfo">
        {from}–{to} of {total}
      </span>
      <button type="button" onClick={() => onPage(page + 1)} disabled={page + 1 >= pages || total === 0}>
        Next →
      </button>
    </div>
  );
}

export interface AdminConsoleProps {
  session: Session | null;
  /** Sub-route: "" (dashboard), "new", or "edit/<objectId>". */
  path: string;
  /** Refresh the session after a management sign-in. */
  onAuthed: () => void;
}

/**
 * The backend management console (CMS + email + activity + AI model). Access is a SEPARATE,
 * management-only login — distinct from the frontend learner accounts. A learner session (or no
 * session) sees the management sign-in surface, never the console.
 */
export function AdminConsole({ session, path, onAuthed }: AdminConsoleProps): React.JSX.Element {
  if (!session || !session.is_admin) {
    return <ManagementLogin session={session} onAuthed={onAuthed} />;
  }
  return (
    <div className="bip-admin-shell">
      <AdminNav path={path} />
      <div className="bip-admin-shell__body">{renderAdminSection(path)}</div>
    </div>
  );
}

function renderAdminSection(path: string): React.JSX.Element {
  if (path.startsWith("edit/")) return <DocumentEditor objectId={path.slice("edit/".length)} />;
  if (path === "new") return <NewDocument />;
  if (path === "assistant") return <AssistantConfig />;
  if (path.startsWith("email")) return <EmailTemplates path={path} />;
  if (path === "outbox") return <OutboxViewer />;
  if (path === "activity") return <AdminActivity />;
  if (path === "content") return <ContentBrowser />;
  return <AdminDashboard />;
}

const ADMIN_NAV: Array<{ href: string; label: string; match: (p: string) => boolean }> = [
  { href: "/admin", label: "Dashboard", match: (p) => p === "" },
  { href: "/admin/content", label: "Content", match: (p) => p === "content" || p === "new" || p.startsWith("edit/") },
  { href: "/admin/email", label: "Email templates", match: (p) => p.startsWith("email") },
  { href: "/admin/outbox", label: "Outbox", match: (p) => p === "outbox" },
  { href: "/admin/activity", label: "Activity", match: (p) => p === "activity" },
  { href: "/admin/assistant", label: "AI model", match: (p) => p === "assistant" },
];

function AdminNav({ path }: { path: string }): React.JSX.Element {
  return (
    <nav className="bip-adminnav" aria-label="Management sections">
      <span className="bip-adminnav__brand">
        <span className="bip-adminnav__badge">Management</span>
      </span>
      <ul>
        {ADMIN_NAV.map((item) => (
          <li key={item.href}>
            <a href={item.href} className={item.match(path) ? "is-active" : ""}>
              {item.label}
            </a>
          </li>
        ))}
      </ul>
      <a className="bip-adminnav__site" href="/">
        View site →
      </a>
    </nav>
  );
}

function AdminDashboard(): React.JSX.Element {
  const [stats, setStats] = useState<Awaited<ReturnType<typeof api.adminStats>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .adminStats()
      .then(setStats)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load stats."));
  }, []);

  const cards: Array<{ label: string; value: number | string; hint?: string }> = stats
    ? [
        { label: "Published lessons", value: stats.documents.toLocaleString() },
        { label: "Topics", value: stats.topics },
        { label: "Courses", value: stats.courses },
        { label: "Learners", value: stats.users, hint: `${stats.confirmed_users} confirmed` },
        { label: "Emails sent", value: stats.emails },
        { label: "Code runs", value: stats.code_runs },
      ]
    : [];

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Dashboard</h1>
        <a href="/admin/new" className="bip-cta">
          + New lesson
        </a>
      </div>
      <p className="bip-lede">An overview of your content, learners, and delivery.</p>
      {error ? <p className="bip-admin__ok">{error}</p> : null}
      <div className="bip-statgrid">
        {(cards.length ? cards : Array.from({ length: 6 }, () => null)).map((c, i) => (
          <div key={c ? c.label : i} className="bip-statcard">
            <span className="bip-statcard__value">{c ? c.value : "—"}</span>
            <span className="bip-statcard__label">{c ? c.label : "Loading…"}</span>
            {c?.hint ? <span className="bip-statcard__hint">{c.hint}</span> : null}
          </div>
        ))}
      </div>

      <div className="bip-dashcols">
        <div className="bip-dashcard">
          <h2>Recent account activity</h2>
          {stats && stats.recent.length > 0 ? (
            <ul className="bip-dashlist">
              {stats.recent.map((e, i) => (
                <li key={`${e.event_type}-${i}`}>
                  <span className="bip-dashlist__type">{e.event_type.replace(/_/g, " ")}</span>
                  <span className="bip-dashlist__detail">{e.detail}</span>
                  <span className="bip-dashlist__time">
                    {new Date(e.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="bip-muted-text">No recent activity.</p>
          )}
        </div>
        <div className="bip-dashcard">
          <h2>Quick actions</h2>
          <div className="bip-quickactions">
            <a href="/admin/new">+ New lesson</a>
            <a href="/admin/content">Manage content</a>
            <a href="/admin/email">Edit email templates</a>
            <a href="/admin/outbox">View email outbox</a>
            <a href="/admin/assistant">Configure AI model</a>
          </div>
        </div>
      </div>
    </section>
  );
}

function ManagementLogin({
  session,
  onAuthed,
}: {
  session: Session | null;
  onAuthed: () => void;
}): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      onAuthed();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bip-admin-login">
      <div className="bip-admin-login__card">
        <span className="bip-admin-login__badge">Management</span>
        <h1>Backend sign in</h1>
        <p className="bip-admin-login__sub">
          {session
            ? "You are signed in as a learner. The management console requires a separate management account."
            : "Sign in with a management account to manage content, email and settings."}
        </p>
        <form className="bip-auth__form" onSubmit={submit}>
          {error ? <p className="bip-auth__alert bip-auth__alert--error">{error}</p> : null}
          <label className="bip-auth__field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="bip-auth__field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <button type="submit" className="bip-auth__submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in to management"}
          </button>
        </form>
        <p className="bip-admin-login__note">
          <a href="/">← Back to Bestinfopages</a>
        </p>
      </div>
    </div>
  );
}

function EmailTemplates({ path }: { path: string }): React.JSX.Element {
  const editing = path.startsWith("email/") ? path.slice("email/".length) : null;
  const [templates, setTemplates] = useState<
    Array<{ template_id: string; version: number; subject: string }>
  >([]);
  const [current, setCurrent] = useState<{
    template_id: string;
    version: number;
    subject: string;
    html_body: string;
    text_body: string;
    required_variables: string[];
  } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (editing) {
      api.emailTemplate(editing).then(setCurrent).catch(() => setCurrent(null));
    } else {
      api.emailTemplates().then((r) => setTemplates(r.templates)).catch(() => setTemplates([]));
    }
  }, [editing]);

  async function save(): Promise<void> {
    if (!current) return;
    setBusy(true);
    setStatus(null);
    try {
      await api.publishEmailTemplate({
        template_id: current.template_id,
        version: current.version + 1,
        subject: current.subject,
        html_body: current.html_body,
        text_body: current.text_body,
        required_variables: current.required_variables,
      });
      setStatus(`Published version ${current.version + 1}.`);
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : "Failed to publish.");
    } finally {
      setBusy(false);
    }
  }

  if (editing && current) {
    return (
      <section aria-labelledby="admin-title">
        <div className="bip-admin__head">
          <h1 id="admin-title">Edit template: {current.template_id}</h1>
          <a href="/admin/email">← Back</a>
        </div>
        <p className="bip-lede">
          Editing publishes an immutable new version (current: v{current.version}). Variables:{" "}
          {current.required_variables.map((v) => `{{${v}}}`).join(", ")}.
        </p>
        <label className="bip-field">
          <span>Subject</span>
          <input
            value={current.subject}
            onChange={(e) => setCurrent({ ...current, subject: e.target.value })}
          />
        </label>
        <label className="bip-field">
          <span>HTML body</span>
          <textarea
            rows={10}
            value={current.html_body}
            onChange={(e) => setCurrent({ ...current, html_body: e.target.value })}
          />
        </label>
        <label className="bip-field">
          <span>Text body</span>
          <textarea
            rows={5}
            value={current.text_body}
            onChange={(e) => setCurrent({ ...current, text_body: e.target.value })}
          />
        </label>
        <button type="button" className="bip-cta" onClick={save} disabled={busy}>
          {busy ? "Publishing…" : "Publish new version"}
        </button>
        {status ? <p className="bip-admin__ok">{status}</p> : null}
      </section>
    );
  }

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Email templates</h1>
        <a href="/admin">← Content</a>
      </div>
      <p className="bip-lede">Transactional email templates. Editing publishes a new version.</p>
      <table className="bip-admin__table">
        <thead>
          <tr>
            <th>Template</th>
            <th>Version</th>
            <th>Subject</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {templates.map((t) => (
            <tr key={t.template_id}>
              <td>{t.template_id}</td>
              <td>v{t.version}</td>
              <td>{t.subject}</td>
              <td>
                <a href={`/admin/email/${encodeURIComponent(t.template_id)}`}>Edit</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function OutboxViewer(): React.JSX.Element {
  const [draft, setDraft] = useState({ status: "", q: "", created_after: "", created_before: "" });
  const [applied, setApplied] = useState(draft);
  const [messages, setMessages] = useState<
    Array<{
      message_id: string;
      to_email: string;
      subject: string;
      status: string;
      created_at: string;
      html_body: string;
    }>
  >([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .outbox({
        status: applied.status || undefined,
        q: applied.q.trim() || undefined,
        created_after: localToIso(applied.created_after),
        created_before: localToIso(applied.created_before),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      .then((r) => {
        setMessages(r.messages);
        setTotal(r.total ?? r.messages.length);
      })
      .catch(() => {
        setMessages([]);
        setTotal(0);
      })
      .finally(() => setLoading(false));
  }, [applied, page]);

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Email outbox</h1>
        <a href="/admin">← Content</a>
      </div>
      <p className="bip-lede">
        Transactional mail recorded for this tenant. Filters run on the server; each page loads{" "}
        {PAGE_SIZE} rows.
      </p>
      <form
        className="bip-admin__filter"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(0);
          setApplied(draft);
        }}
      >
        <label>
          Status
          <select
            value={draft.status}
            onChange={(e) => setDraft({ ...draft, status: e.target.value })}
            aria-label="Status filter"
          >
            <option value="">All statuses</option>
            {OUTBOX_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Search
          <input
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            placeholder="To, subject, or template"
            aria-label="Search outbox"
          />
        </label>
        <label>
          From
          <input
            type="datetime-local"
            value={draft.created_after}
            onChange={(e) => setDraft({ ...draft, created_after: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="datetime-local"
            value={draft.created_before}
            onChange={(e) => setDraft({ ...draft, created_before: e.target.value })}
          />
        </label>
        <button type="submit">Apply</button>
        <button
          type="button"
          onClick={() => {
            const empty = { status: "", q: "", created_after: "", created_before: "" };
            setDraft(empty);
            setPage(0);
            setApplied(empty);
          }}
        >
          Clear
        </button>
      </form>
      {loading ? <p>Loading…</p> : null}
      <table className="bip-admin__table">
        <thead>
          <tr>
            <th>To</th>
            <th>Subject</th>
            <th>Status</th>
            <th>When</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {messages.map((m) => (
            <tr key={m.message_id}>
              <td>{m.to_email}</td>
              <td>{m.subject}</td>
              <td>
                <span className={`bip-badge bip-badge--${m.status}`}>{m.status}</span>
              </td>
              <td>{formatWhen(m.created_at)}</td>
              <td>
                <button
                  type="button"
                  className="bip-linklike"
                  onClick={() => setOpen(open === m.message_id ? null : m.message_id)}
                >
                  {open === m.message_id ? "Hide" : "View"}
                </button>
              </td>
            </tr>
          ))}
          {!loading && messages.length === 0 ? (
            <tr>
              <td colSpan={5}>No messages found.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <AdminPager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
      {open ? (
        <div
          className="bip-outbox__preview"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: admin-only preview of our own template output
          dangerouslySetInnerHTML={{
            __html: messages.find((m) => m.message_id === open)?.html_body ?? "",
          }}
        />
      ) : null}
    </section>
  );
}

function AdminActivity(): React.JSX.Element {
  const [draft, setDraft] = useState({
    event_type: "",
    q: "",
    created_after: "",
    created_before: "",
  });
  const [applied, setApplied] = useState(draft);
  const [events, setEvents] = useState<
    Array<{ event_type: string; created_at: string; subject_id?: string; detail: string | null }>
  >([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .adminActivity({
        event_type: applied.event_type || undefined,
        q: applied.q.trim() || undefined,
        created_after: localToIso(applied.created_after),
        created_before: localToIso(applied.created_before),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      .then((r) => {
        setEvents(r.events);
        setTotal(r.total ?? r.events.length);
      })
      .catch(() => {
        setEvents([]);
        setTotal(0);
      })
      .finally(() => setLoading(false));
  }, [applied, page]);

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Account activity</h1>
        <a href="/admin">← Content</a>
      </div>
      <p className="bip-lede">
        Registrations, confirmations, logins and password resets. Filters run on the server; each
        page loads {PAGE_SIZE} events.
      </p>
      <form
        className="bip-admin__filter"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(0);
          setApplied(draft);
        }}
      >
        <label>
          Event
          <select
            value={draft.event_type}
            onChange={(e) => setDraft({ ...draft, event_type: e.target.value })}
            aria-label="Event type"
          >
            <option value="">All events</option>
            {ACTIVITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Search
          <input
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            placeholder="Email, subject id, or event"
            aria-label="Search activity"
          />
        </label>
        <label>
          From
          <input
            type="datetime-local"
            value={draft.created_after}
            onChange={(e) => setDraft({ ...draft, created_after: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="datetime-local"
            value={draft.created_before}
            onChange={(e) => setDraft({ ...draft, created_before: e.target.value })}
          />
        </label>
        <button type="submit">Apply</button>
        <button
          type="button"
          onClick={() => {
            const empty = { event_type: "", q: "", created_after: "", created_before: "" };
            setDraft(empty);
            setPage(0);
            setApplied(empty);
          }}
        >
          Clear
        </button>
      </form>
      {loading ? <p>Loading…</p> : null}
      <table className="bip-admin__table">
        <thead>
          <tr>
            <th>Event</th>
            <th>Detail</th>
            <th>Subject</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={`${e.event_type}-${e.created_at}-${i}`}>
              <td>{e.event_type}</td>
              <td>{e.detail}</td>
              <td className="bip-mono">{e.subject_id?.slice(0, 8)}</td>
              <td>{formatWhen(e.created_at)}</td>
            </tr>
          ))}
          {!loading && events.length === 0 ? (
            <tr>
              <td colSpan={4}>No events found.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <AdminPager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
    </section>
  );
}

function AssistantConfig(): React.JSX.Element {
  const [models, setModels] = useState<Array<{ id: string; label: string; kind: string }>>([]);
  const [active, setActive] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .assistantModels()
      .then((r) => {
        setModels(r.models);
        setActive(r.active);
      })
      .catch(() => undefined);
  }, []);

  async function save(id: string): Promise<void> {
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.setAssistantModel(id);
      setActive(r.active);
      setStatus("Saved.");
    } catch {
      setStatus("Failed to save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">AI assistant model</h1>
        <a href="/admin">← Back</a>
      </div>
      <p className="bip-lede">
        Choose the model the AI tutor uses to answer learner questions (endpoints from models.txt).
      </p>
      <ul className="bip-modellist">
        {models.map((m) => (
          <li key={m.id} className={m.id === active ? "is-active" : ""}>
            <label>
              <input
                type="radio"
                name="assistant-model"
                checked={m.id === active}
                onChange={() => save(m.id)}
                disabled={busy}
              />
              <span className="bip-modellist__label">{m.label}</span>
              <span className="bip-modellist__kind">{m.kind}</span>
            </label>
          </li>
        ))}
      </ul>
      {status ? <p className="bip-admin__ok">{status}</p> : null}
    </section>
  );
}

type ContentFilters = {
  subject: string;
  phase_title: string;
  category: string;
  kind: string;
  q: string;
  published_after: string;
  published_before: string;
  sort: "recent" | "order";
};

const EMPTY_CONTENT: ContentFilters = {
  subject: "",
  phase_title: "",
  category: "",
  kind: "",
  q: "",
  published_after: "",
  published_before: "",
  sort: "recent",
};

function ContentBrowser(): React.JSX.Element {
  const [draft, setDraft] = useState<ContentFilters>(EMPTY_CONTENT);
  const [applied, setApplied] = useState<ContentFilters>(EMPTY_CONTENT);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(0);
  const [subjects, setSubjects] = useState<TermCount[]>([]);
  const [courses, setCourses] = useState<TermCount[]>([]);
  const [categories, setCategories] = useState<TermCount[]>([]);
  const [kinds, setKinds] = useState<TermCount[]>([]);

  useEffect(() => {
    Promise.all([
      api.taxonomy("subject"),
      api.taxonomy("phase_title"),
      api.taxonomy("category"),
      api.taxonomy("kind"),
    ])
      .then(([subjectTerms, courseTerms, categoryTerms, kindTerms]) => {
        setSubjects([...subjectTerms.terms].sort((a, b) => a.term.localeCompare(b.term)));
        setCourses([...courseTerms.terms].sort((a, b) => a.term.localeCompare(b.term)));
        setCategories([...categoryTerms.terms].sort((a, b) => compareTerms(a.term, b.term)));
        setKinds([...kindTerms.terms].sort((a, b) => a.term.localeCompare(b.term)));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    setLoading(true);
    api
      .catalog({
        subject: applied.subject || undefined,
        phase_title: applied.phase_title || undefined,
        category: applied.category || undefined,
        kind: applied.kind || undefined,
        q: applied.q.trim() || undefined,
        published_after: localToIso(applied.published_after),
        published_before: localToIso(applied.published_before),
        sort: applied.sort,
        include_total: true,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      .then((r) => {
        setEntries(r.entries);
        setTotal(r.total ?? r.entries.length);
      })
      .catch(() => {
        setEntries([]);
        setTotal(0);
      })
      .finally(() => setLoading(false));
  }, [applied, page]);

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Content manager</h1>
        <div className="bip-admin__headactions">
          <a href="/admin/new" className="bip-cta">
            + New lesson
          </a>
        </div>
      </div>
      <p className="bip-lede">
        Create, edit, and publish curriculum documents. Filters run on the server; each page loads{" "}
        {PAGE_SIZE} documents. Newest publications are listed first so later series are not buried
        under Python C00.
      </p>
      <form
        className="bip-admin__filter"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(0);
          setApplied(draft);
        }}
      >
        <label>
          Subject
          <select
            value={draft.subject}
            onChange={(e) => setDraft({ ...draft, subject: e.target.value })}
          >
            <option value="">All subjects</option>
            {subjects.map((t) => (
              <option key={t.term} value={t.term}>
                {subjectLabel(t.term)} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Course
          <select
            value={draft.phase_title}
            onChange={(e) => setDraft({ ...draft, phase_title: e.target.value })}
          >
            <option value="">All courses</option>
            {courses.map((t) => (
              <option key={t.term} value={t.term}>
                {t.term} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Topic
          <select
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value })}
          >
            <option value="">All topics</option>
            {categories.map((t) => (
              <option key={t.term} value={t.term}>
                {t.term} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Kind
          <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            <option value="">All kinds</option>
            {kinds.map((t) => (
              <option key={t.term} value={t.term}>
                {kindLabel(t.term)} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Title
          <input
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            placeholder="Search titles"
            aria-label="Title search"
          />
        </label>
        <label>
          Published from
          <input
            type="datetime-local"
            value={draft.published_after}
            onChange={(e) => setDraft({ ...draft, published_after: e.target.value })}
          />
        </label>
        <label>
          Published to
          <input
            type="datetime-local"
            value={draft.published_before}
            onChange={(e) => setDraft({ ...draft, published_before: e.target.value })}
          />
        </label>
        <label>
          Sort
          <select
            value={draft.sort}
            onChange={(e) => setDraft({ ...draft, sort: e.target.value as ContentFilters["sort"] })}
          >
            <option value="recent">Recently published</option>
            <option value="order">Curriculum order</option>
          </select>
        </label>
        <button type="submit">Apply</button>
        <button
          type="button"
          onClick={() => {
            setDraft(EMPTY_CONTENT);
            setPage(0);
            setApplied(EMPTY_CONTENT);
          }}
        >
          Clear
        </button>
      </form>
      {loading ? <p>Loading…</p> : null}
      <table className="bip-admin__table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Kind</th>
            <th>Subject</th>
            <th>Course</th>
            <th>Topic</th>
            <th>Published</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.object_id}>
              <td>{cleanLessonTitle(e.title)}</td>
              <td>{e.terms.kind?.[0] ? kindLabel(e.terms.kind[0]) : e.document_type}</td>
              <td>{e.terms.subject?.[0] ? subjectLabel(e.terms.subject[0]) : "—"}</td>
              <td>{e.terms.phase_title?.[0] ?? "—"}</td>
              <td>{e.terms.category?.[0] ?? "—"}</td>
              <td>{formatWhen(e.published_at)}</td>
              <td>
                <a href={`/admin/edit/${e.object_id}`}>Edit</a>
              </td>
            </tr>
          ))}
          {!loading && entries.length === 0 ? (
            <tr>
              <td colSpan={7}>No documents found.</td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <AdminPager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
    </section>
  );
}

function NewDocument(): React.JSX.Element {
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(): Promise<void> {
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createDocument({
        document_type: "tutorial",
        locale: "en",
        title: title.trim(),
        blocks: [emptyBlock("paragraph")],
      });
      if (category.trim()) {
        await api.assignTaxonomy(created.object_id, "category", category.trim().toUpperCase());
        await api.assignTaxonomy(created.object_id, "kind", "lesson");
      }
      // Publish an initial revision so the editor can load it (there is no read-draft endpoint).
      await api.submitDocument(created.object_id).catch(() => undefined);
      await api.publishDocument(created.object_id, { title: title.trim() });
      window.location.hash = `/admin/edit/${created.object_id}`;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-title">
      <h1 id="admin-title">New lesson</h1>
      <div className="bip-admin__form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Category (optional)
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="C12"
          />
        </label>
        {error ? <p className="bip-comment__error">{error}</p> : null}
        <div className="bip-admin__actions">
          <button
            type="button"
            className="bip-comment__submit"
            disabled={busy || !title.trim()}
            onClick={create}
          >
            {busy ? "Creating…" : "Create & edit"}
          </button>
          <a href="/admin">Cancel</a>
        </div>
      </div>
    </section>
  );
}

function DocumentEditor({ objectId }: { objectId: string }): React.JSX.Element {
  const [title, setTitle] = useState("");
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [lifecycle, setLifecycle] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .getDocument(objectId)
      .then(async (doc) => {
        setLifecycle(doc.lifecycle);
        if (doc.latest_revision_id) {
          const rev = await api.revision(doc.latest_revision_id);
          setTitle(rev.title);
          setBlocks(rev.blocks);
        } else {
          setBlocks([emptyBlock("paragraph")]);
        }
      })
      .catch((e) => setError(e?.message ?? "Failed to load"))
      .finally(() => setLoading(false));
  }, [objectId]);

  const saveDraft = useCallback(async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await api.editDraft(objectId, blocks);
      setStatus("Draft saved.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to save draft");
    } finally {
      setBusy(false);
    }
  }, [objectId, blocks]);

  const publish = useCallback(async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await api.editDraft(objectId, blocks);
      await api.submitDocument(objectId).catch(() => undefined);
      const res = await api.publishDocument(objectId, { title });
      setStatus(`Published revision ${res.revision_id.slice(0, 8)}.`);
      setLifecycle("published");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to publish");
    } finally {
      setBusy(false);
    }
  }, [objectId, blocks, title]);

  if (loading) {
    return (
      <section aria-labelledby="admin-title">
        <h1 id="admin-title">Loading…</h1>
      </section>
    );
  }

  return (
    <section aria-labelledby="admin-title">
      <div className="bip-admin__head">
        <h1 id="admin-title">Edit document</h1>
        <a href="/admin">← Back</a>
      </div>
      <p className="bip-admin__meta">
        Status: <strong>{lifecycle || "draft"}</strong> · {objectId.slice(0, 8)}
      </p>
      <label className="bip-admin__titlefield">
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>

      <BlockEditor blocks={blocks} onChange={setBlocks} />

      <div className="bip-admin__bar">
        {error ? <span className="bip-comment__error">{error}</span> : null}
        {status ? <span className="bip-admin__ok">{status}</span> : null}
        <div className="bip-admin__actions">
          <button type="button" onClick={saveDraft} disabled={busy}>
            Save draft
          </button>
          <button type="button" className="bip-comment__submit" onClick={publish} disabled={busy}>
            {busy ? "Working…" : "Publish"}
          </button>
        </div>
      </div>
    </section>
  );
}
