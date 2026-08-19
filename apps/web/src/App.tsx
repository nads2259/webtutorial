import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CatalogEntry,
  type Revision,
  type SearchHit,
  type Session,
} from "./api/client";
import { AuthoringPage } from "./AuthoringPage";
import { SimulationPage } from "./SimulationPage";
import { CategorySidebar } from "./components/CategorySidebar";
import { CommentsPanel } from "./components/CommentsPanel";
import { DocsHeader } from "./components/DocsHeader";
import { LessonPager } from "./components/LessonPager";
import { LessonReader } from "./components/LessonReader";
import { AdminConsole } from "./components/admin/AdminConsole";
import { AssistantPanel } from "./components/AssistantPanel";
import { AuthPages } from "./components/AuthPages";
import { CourseDetail } from "./components/CourseDetail";
import { Landing } from "./components/marketing/Landing";
import { Seo } from "./components/Seo";
import { reducedMotionCss } from "./a11y/reduced-motion";
import { navigate } from "./lib/nav";
import {
  type Course,
  type SubjectOutline,
  buildOutline,
  cleanLessonTitle,
  flatten,
  groupByModule,
  levelForCourse,
  subjectLabel,
} from "./lib/tree";

type AuthView = "login" | "register" | "forgot" | "reset" | "confirm";

type Route =
  | { view: "home"; subject?: string }
  | { view: "category"; category: string }
  | { view: "lesson"; category: string; revisionId: string }
  | { view: "search"; query: string }
  | { view: "activity" }
  | { view: "admin"; path: string }
  | { view: "auth"; auth: AuthView }
  | { view: "demo-authoring" }
  | { view: "demo-simulation" };

function parseLocation(): Route {
  const path = (typeof window !== "undefined" ? window.location.pathname : "/") || "/";
  const parts = path.replace(/^\//, "").split("/").filter(Boolean);
  switch (parts[0]) {
    case "c":
      return parts[1] ? { view: "category", category: parts[1] } : { view: "home" };
    case "l":
      return parts[1] && parts[2]
        ? { view: "lesson", category: parts[1], revisionId: parts.slice(2).join("/") }
        : { view: "home" };
    case "search":
      return { view: "search", query: decodeURIComponent(parts.slice(1).join("/")) };
    case "activity":
      return { view: "activity" };
    case "admin":
      return { view: "admin", path: parts.slice(1).join("/") };
    case "s":
      return parts[1] ? { view: "home", subject: parts[1] } : { view: "home" };
    case "login":
      return { view: "auth", auth: "login" };
    case "register":
      return { view: "auth", auth: "register" };
    case "forgot-password":
      return { view: "auth", auth: "forgot" };
    case "reset-password":
      return { view: "auth", auth: "reset" };
    case "confirm":
      return { view: "auth", auth: "confirm" };
    case "demo":
      return parts[1] === "simulation"
        ? { view: "demo-simulation" }
        : { view: "demo-authoring" };
    default:
      return { view: "home" };
  }
}

function firstParagraph(blocks: Revision["blocks"]): string {
  for (const b of blocks) {
    if (b.type === "paragraph" && typeof b.data.content === "string" && b.data.content.trim()) {
      return b.data.content.trim().replace(/\*\*/g, "").slice(0, 300);
    }
  }
  return "";
}

/** Intercept internal <a href="/…"> clicks for SPA navigation (keeps URLs real + crawlable). */
function onShellClick(e: React.MouseEvent<HTMLDivElement>): void {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
    return;
  }
  const anchor = (e.target as HTMLElement).closest("a");
  const href = anchor?.getAttribute("href");
  if (!anchor || !href) return;
  if (!href.startsWith("/") || href.startsWith("/api") || anchor.target === "_blank") return;
  e.preventDefault();
  navigate(href);
}

export function App(): React.JSX.Element {
  const [route, setRoute] = useState<Route>(parseLocation);
  const [session, setSession] = useState<Session | null>(null);
  const [subjects, setSubjects] = useState<SubjectOutline[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [subjectOf, setSubjectOf] = useState<Record<string, string>>({});
  const [catLabels, setCatLabels] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [catalogCategory, setCatalogCategory] = useState<string | null>(null);
  const [lessonRefIndex, setLessonRefIndex] = useState<Record<string, { r: string; t: string }>>(
    {},
  );
  const [revision, setRevision] = useState<Revision | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SearchHit[]>([]);
  const [runs, setRuns] = useState<Awaited<ReturnType<typeof api.myRuns>>["runs"]>([]);
  const [accountEvents, setAccountEvents] = useState<
    Awaited<ReturnType<typeof api.myActivity>>["events"]
  >([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const onNav = (): void => setRoute(parseLocation());
    window.addEventListener("popstate", onNav);
    return () => window.removeEventListener("popstate", onNav);
  }, []);

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(null));
    // Build the course outline (phases -> categories) from per-category overview docs + counts.
    Promise.all([api.catalog({ kind: "overview", limit: 1000 }), api.categories()])
      .then(([ov, cats]) => {
        const counts: Record<string, number> = {};
        for (const t of cats.terms) counts[t.term] = t.count;
        const overviews = ov.entries.filter((e) => (e.terms.module?.length ?? 0) === 0);
        const { subjects: builtSubjects, courses: built, labels, subjectOf: builtSubjectOf } =
          buildOutline(overviews, counts);
        setSubjects(builtSubjects);
        setCourses(built);
        setCatLabels(labels);
        setSubjectOf(builtSubjectOf);
      })
      .catch(() => undefined);
    // Global lesson index for cross-reference hyperlinks (fetched once, then cached in state).
    api
      .lessonIndex()
      .then((r) => setLessonRefIndex(r.lessons))
      .catch(() => undefined);
  }, []);

  const labelFor = useCallback(
    (code: string): string => catLabels[code] ?? code,
    [catLabels],
  );

  const activeCategory =
    route.view === "category" || route.view === "lesson" ? route.category : null;

  // Load the active category's catalog (drives the sidebar tree + prev/next pager).
  useEffect(() => {
    if (!activeCategory || activeCategory === catalogCategory) return;
    api
      .catalog({ category: activeCategory, limit: 1000 })
      .then((r) => {
        setCatalog(r.entries);
        setCatalogCategory(activeCategory);
      })
      .catch(() => undefined);
  }, [activeCategory, catalogCategory]);

  // Load the current lesson revision.
  useEffect(() => {
    if (route.view !== "lesson") return;
    setLoading(true);
    setError(null);
    setRevision(null);
    api
      .revision(route.revisionId)
      .then(setRevision)
      .catch((e) => setError(e?.message ?? "Failed to load lesson"))
      .finally(() => setLoading(false));
  }, [route]);

  // Search + activity loaders.
  useEffect(() => {
    if (route.view !== "search" || !route.query) {
      if (route.view === "search") setResults([]);
      return;
    }
    setLoading(true);
    api
      .search(route.query)
      .then((r) => setResults(r.results))
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [route]);

  useEffect(() => {
    if (route.view !== "activity") return;
    api.myRuns().then((r) => setRuns(r.runs)).catch(() => setRuns([]));
    api.myActivity().then((r) => setAccountEvents(r.events)).catch(() => setAccountEvents([]));
  }, [route, session]);

  // Move focus to main on route change (accessibility) and close the mobile drawer.
  useEffect(() => {
    mainRef.current?.focus();
    setMenuOpen(false);
  }, [route]);

  const groups = useMemo(() => groupByModule(catalog), [catalog]);
  const flat = useMemo(() => flatten(catalog), [catalog]);
  const onSearch = useCallback((q: string) => {
    if (q) navigate(`/search/${encodeURIComponent(q)}`);
  }, []);
  const onLogout = useCallback(async () => {
    await api.logout();
    setSession(null);
    navigate("/");
  }, []);

  if (route.view === "demo-authoring") return <AuthoringPage />;
  if (route.view === "demo-simulation") return <SimulationPage />;
  if (route.view === "auth") {
    return (
      <AuthPages
        view={route.auth}
        onAuthed={() => {
          api.session().then(setSession).catch(() => setSession(null));
          navigate("/");
        }}
      />
    );
  }

  const activeRevisionId = route.view === "lesson" ? route.revisionId : null;
  const lessonIndex = flat.findIndex((l) => l.revisionId === activeRevisionId);
  const prev = lessonIndex > 0 ? flat[lessonIndex - 1] : null;
  const next = lessonIndex >= 0 && lessonIndex < flat.length - 1 ? flat[lessonIndex + 1] : null;
  const currentLessonId =
    lessonIndex >= 0 ? (flat[lessonIndex].entry.terms.lesson?.[0] ?? null) : null;

  const homeSubject = route.view === "home" ? route.subject : undefined;
  const categorySubject = activeCategory ? subjectOf[activeCategory] : undefined;
  const activeSubject = homeSubject ?? categorySubject;
  const allCourses = subjects.length ? subjects.flatMap((s) => s.courses) : courses;
  const scopedCourses = activeSubject
    ? (subjects.find((s) => s.id === activeSubject)?.courses ?? courses)
    : courses;
  const landingCourses = homeSubject ? scopedCourses : allCourses;

  return (
    <div className="ns-shell bip-docs" onClickCapture={onShellClick}>
      <style>{reducedMotionCss}</style>
      <a className="ns-skip" href="#main-content" data-testid="skip-link">
        Skip to main content
      </a>
      <DocsHeader
        session={session}
        subjects={subjects}
        activeSubject={activeSubject}
        showMenuButton={route.view !== "home"}
        initialQuery={route.view === "search" ? route.query : ""}
        onSearch={onSearch}
        onToggleMenu={() => setMenuOpen((v) => !v)}
        onLogout={onLogout}
      />
      <div className={`bip-docs__body${route.view === "home" ? " bip-docs__body--full" : ""}`}>
        {route.view === "home" ? null : (
          <CategorySidebar
            courses={scopedCourses}
            activeCategory={activeCategory}
            activeRevisionId={activeRevisionId}
            groups={groups}
            labelFor={labelFor}
            backHref={categorySubject ? `/s/${categorySubject}` : "/"}
            open={menuOpen}
            onClose={() => setMenuOpen(false)}
          />
        )}
        <main id="main-content" ref={mainRef} tabIndex={-1} className="ns-main bip-docs__main">
          {renderView({
            route,
            courses: scopedCourses,
            landingCourses,
            subjects,
            homeSubject,
            catalog,
            groups,
            revision,
            loading,
            error,
            results,
            runs,
            accountEvents,
            session,
            onAuthed: () => api.session().then(setSession).catch(() => setSession(null)),
            labelFor,
            lessonIndex: lessonRefIndex,
            catLabels,
            currentLessonId,
            categorySubject,
            pager:
              route.view === "lesson" ? (
                <LessonPager category={route.category} prev={prev} next={next} />
              ) : null,
          })}
        </main>
      </div>
      <footer className="ns-footer bip-docs__footer">
        <div className="ns-footer__inner">
          <span className="bip-wordmark">Bestinfopages</span>
          <p>Learn by reading, searching, and running real code — every run is tracked.</p>
        </div>
        <div className="ns-footer__bar">© 2026 Bestinfopages.</div>
      </footer>
      <AssistantPanel
        lessonObjectId={route.view === "lesson" && revision ? revision.object_id : null}
      />
    </div>
  );
}

interface ViewProps {
  route: Route;
  courses: Course[];
  landingCourses: Course[];
  subjects: SubjectOutline[];
  homeSubject?: string;
  catalog: CatalogEntry[];
  groups: ReturnType<typeof groupByModule>;
  revision: Revision | null;
  loading: boolean;
  error: string | null;
  results: SearchHit[];
  runs: Awaited<ReturnType<typeof api.myRuns>>["runs"];
  accountEvents: Awaited<ReturnType<typeof api.myActivity>>["events"];
  session: Session | null;
  onAuthed: () => void;
  labelFor: (code: string) => string;
  lessonIndex: Record<string, { r: string; t: string }>;
  catLabels: Record<string, string>;
  currentLessonId: string | null;
  categorySubject?: string;
  pager: React.ReactNode;
}

function levelForCategory(courses: Course[], code: string): string {
  const idx = courses.findIndex((c) => c.categories.some((cat) => cat.code === code));
  return levelForCourse(idx < 0 ? 0 : idx, courses.length || 1);
}

function renderView(p: ViewProps): React.JSX.Element {
  switch (p.route.view) {
    case "home":
      return (
        <Landing
          courses={p.landingCourses}
          subjects={p.subjects}
          subject={p.homeSubject}
          session={p.session}
        />
      );
    case "category":
      return (
        <CourseDetail
          code={p.route.category}
          title={p.labelFor(p.route.category)}
          catalog={p.catalog}
          groups={p.groups}
          level={levelForCategory(p.courses, p.route.category)}
          subject={p.categorySubject}
        />
      );
    case "lesson":
      if (p.error) return <ErrorView message={p.error} />;
      if (p.loading || !p.revision) return <LoadingView title="Loading lesson…" />;
      return (
        <>
          <Seo
            title={p.revision.title}
            description={firstParagraph(p.revision.blocks) || `Learn: ${p.revision.title}`}
            path={`/l/${p.route.category}/${p.route.revisionId}`}
            type="article"
            jsonLd={{
              "@context": "https://schema.org",
              "@type": "LearningResource",
              name: p.revision.title,
              description: firstParagraph(p.revision.blocks) || p.revision.title,
              learningResourceType: "lesson",
              inLanguage: "en",
              isPartOf: { "@type": "Course", name: p.labelFor(p.route.category) },
            }}
          />
          <nav className="bip-crumbs" aria-label="Breadcrumb">
            {p.categorySubject ? (
              <>
                <a href={`/s/${p.categorySubject}`}>{subjectLabel(p.categorySubject)}</a>
                <span aria-hidden="true">/</span>
              </>
            ) : null}
            <a href={`/c/${p.route.category}`}>{p.labelFor(p.route.category)}</a>
            <span aria-hidden="true">/</span>
            <span aria-current="page">{cleanLessonTitle(p.revision.title)}</span>
          </nav>
          <LessonReader
            revision={p.revision}
            authenticated={!!p.session}
            lessonId={p.currentLessonId}
            allowPythonRun={/^C\d{2}$/i.test(p.route.category)}
            inline={{
              index: p.lessonIndex,
              catLabels: p.catLabels,
              currentLessonId: p.currentLessonId ? p.currentLessonId.toUpperCase() : null,
            }}
          />
          {p.pager}
          <CommentsPanel
            objectId={p.revision.object_id}
            revisionId={p.revision.revision_id}
            anchorBlockId={p.revision.blocks[0]?.id ?? null}
            authenticated={!!p.session}
            subjectId={p.session?.subject_id ?? null}
          />
        </>
      );
    case "search":
      return <SearchView query={p.route.query} results={p.results} loading={p.loading} />;
    case "activity":
      return (
        <ActivityView runs={p.runs} accountEvents={p.accountEvents} session={p.session} />
      );
    case "admin":
      return <AdminConsole session={p.session} path={p.route.path} onAuthed={p.onAuthed} />;
    default:
      return (
        <Landing
          courses={p.landingCourses}
          subjects={p.subjects}
          subject={p.homeSubject}
          session={p.session}
        />
      );
  }
}

function SearchView({
  query,
  results,
  loading,
}: {
  query: string;
  results: SearchHit[];
  loading: boolean;
}): React.JSX.Element {
  return (
    <section aria-labelledby="search-title">
      <h1 id="search-title">Search</h1>
      <p>
        {query ? (
          <>
            Results for <strong>{query}</strong>
          </>
        ) : (
          "Type in the search box above to find lessons."
        )}
      </p>
      {loading ? <p>Searching…</p> : null}
      <ul className="bip-results">
        {results.map((r) => (
          <li key={r.block_id}>
            <a href={`/l/_/${r.revision_id}`} className="bip-result">
              <span className="bip-result__text">{r.text.slice(0, 240)}</span>
              <span className="bip-result__score">score {r.score.toFixed(3)}</span>
            </a>
          </li>
        ))}
      </ul>
      {!loading && query && results.length === 0 ? <p>No matches found.</p> : null}
    </section>
  );
}

const ACCOUNT_EVENT_LABELS: Record<string, string> = {
  registered: "Account created",
  email_confirmed: "Email confirmed",
  login: "Signed in",
  password_reset_requested: "Password reset requested",
  password_reset: "Password changed",
  confirmation_resent: "Confirmation email re-sent",
};

function ActivityView({
  runs,
  accountEvents,
  session,
}: {
  runs: ViewProps["runs"];
  accountEvents: ViewProps["accountEvents"];
  session: Session | null;
}): React.JSX.Element {
  if (!session) {
    return (
      <section aria-labelledby="activity-title">
        <h1 id="activity-title">My activity</h1>
        <p>
          <a href="/login">Sign in</a> to see your account activity and the code you have run.
        </p>
      </section>
    );
  }
  return (
    <section aria-labelledby="activity-title">
      <h1 id="activity-title">My activity</h1>

      <h2 className="bip-activity__section">Account</h2>
      {accountEvents.length === 0 ? (
        <p>No account activity yet.</p>
      ) : (
        <ul className="bip-activity">
          {accountEvents.map((e, i) => (
            <li key={`${e.event_type}-${i}`} className="bip-activity__item bip-activity__item--event">
              <div className="bip-activity__meta">
                <span className="bip-activity__outcome">
                  {ACCOUNT_EVENT_LABELS[e.event_type] ?? e.event_type}
                </span>
                <span>{new Date(e.created_at).toLocaleString()}</span>
                {e.detail ? <span>{e.detail}</span> : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      <h2 className="bip-activity__section">Code runs</h2>
      {runs.length === 0 ? (
        <p>No runs yet. Open a lesson and run a Python example.</p>
      ) : (
        <ul className="bip-activity">
          {runs.map((r) => (
            <li key={r.run_id} className={`bip-activity__item bip-activity__item--${r.outcome}`}>
              <div className="bip-activity__meta">
                <span className="bip-activity__outcome">{r.outcome}</span>
                <span>exit {r.exit_code}</span>
                <span>{r.duration_ms} ms</span>
                <span>{new Date(r.created_at).toLocaleString()}</span>
                {r.lesson_id ? <span>{r.lesson_id}</span> : null}
              </div>
              {r.stdout ? <pre className="bip-runner__stdout">{r.stdout.slice(0, 400)}</pre> : null}
              {r.stderr ? <pre className="bip-runner__stderr">{r.stderr.slice(0, 400)}</pre> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function LoadingView({ title }: { title: string }): React.JSX.Element {
  return (
    <section aria-labelledby="loading-title">
      <h1 id="loading-title">{title}</h1>
      <p>Please wait…</p>
    </section>
  );
}

function ErrorView({ message }: { message: string }): React.JSX.Element {
  return (
    <section aria-labelledby="error-title">
      <h1 id="error-title">Something went wrong</h1>
      <p>{message}</p>
      <p>
        <a href="/">Back to home</a>
      </p>
    </section>
  );
}
