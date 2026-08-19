import { useState } from "react";
import type { Session } from "../../api/client";
import { HERO_IMG, thumbFor } from "../../lib/assets";
import { navigate } from "../../lib/nav";
import { type Course, type SubjectOutline, cleanBlurb, estHours, levelForCourse, subjectLabel } from "../../lib/tree";
import { CourseCard } from "../CourseCard";
import { Seo } from "../Seo";

interface FlatCat {
  code: string;
  title: string;
  count: number;
  summary?: string | null;
  courseIndex: number;
}

const FEATURES: Array<{ icon: string; title: string; body: string }> = [
  { icon: "✦", title: "AI tutor on every page", body: "Ask questions in context and get grounded, cited answers as you read." },
  { icon: "▶", title: "Runnable Python sandbox", body: "Execute real code in Python lessons — no setup, and every run is tracked." },
  { icon: "◆", title: "Diagrams that clarify", body: "Concept maps and flowcharts render inline to make ideas click faster." },
  { icon: "◈", title: "Structured learning paths", body: "Language, AI systems, and engineering books — organized subject by subject." },
  { icon: "◎", title: "Progress you can see", body: "Your activity and code runs are saved so you always know where you left off." },
  { icon: "❤", title: "Free to learn", body: "Create an account and start learning — the entire curriculum is open." },
];

const STEPS: Array<{ n: string; title: string; body: string }> = [
  { n: "1", title: "Read", body: "Short, focused lessons with clear examples and inline diagrams." },
  { n: "2", title: "Practice", body: "Run Python in the sandbox where the lesson is code; use labs and review questions everywhere else." },
  { n: "3", title: "Master", body: "Check understanding with exercises, quizzes, and the AI tutor." },
];

const FAQ: Array<{ q: string; a: string }> = [
  {
    q: "Do I need any prior experience?",
    a: "No. Start with Python fundamentals, or jump to LangGraph, AI systems, or an engineering book if you already know the language.",
  },
  {
    q: "Is it really free?",
    a: "Yes — create a free account and the full curriculum is available. Some interactive features like running code and the AI tutor work best when signed in.",
  },
  {
    q: "How is this different from a video course?",
    a: "Every lesson is written to work through, not watch: diagrams, examples, labs, and an AI tutor grounded in the material. Python lessons can run in the browser sandbox.",
  },
  {
    q: "Can I get help when I'm stuck?",
    a: "Yes. The AI tutor is available on every page and answers using the course content, with links to the exact lessons it references.",
  },
];

const SERIES_PATHS: Array<{ title: string; ids: string[] }> = [
  { title: "Search and organizational knowledge", ids: ["knowledge-base", "rag", "postgresql"] },
  { title: "Architecture and platform engineering", ids: ["system-design", "postgresql", "agentic-release"] },
  { title: "Quality and delivery automation", ids: ["system-design", "agentic-qa", "agentic-release"] },
  { title: "Complete agentic platform", ids: ["system-design", "knowledge-base", "rag", "agentic-qa", "agentic-release", "postgresql"] },
];

function pathCard(course: Course, i: number): React.JSX.Element {
  const lessons = course.categories.reduce((n, c) => n + c.count, 0);
  const href = course.categories[0] ? `/c/${course.categories[0].code}` : "/";
  return (
    <a key={`${course.id}-${i}`} className="bip-path" href={href}>
      <span className="bip-path__num">Course {i + 1}</span>
      <span className="bip-path__title">{course.title}</span>
      <span className="bip-path__meta">
        {course.categories.length} topics · {lessons} lessons · ~{estHours(lessons)}h
      </span>
    </a>
  );
}

function featuredBySubject(subjects: SubjectOutline[]): FlatCat[] {
  const out: FlatCat[] = [];
  for (const s of subjects) {
    const course = s.courses[0];
    const cat = course?.categories[0];
    if (!cat) continue;
    out.push({
      code: cat.code,
      title: `${s.label}: ${cat.title}`,
      count: cat.count,
      summary: cat.summary ?? null,
      courseIndex: 0,
    });
  }
  return out;
}

function flatten(courses: Course[]): FlatCat[] {
  const out: FlatCat[] = [];
  courses.forEach((course, i) =>
    course.categories.forEach((c) =>
      out.push({
        code: c.code,
        title: c.title,
        count: c.count,
        summary: c.summary ?? null,
        courseIndex: i,
      }),
    ),
  );
  return out;
}

export interface LandingProps {
  courses: Course[];
  subjects?: SubjectOutline[];
  subject?: string;
  session: Session | null;
}

export function Landing({
  courses,
  subjects = [],
  subject,
  session,
}: LandingProps): React.JSX.Element {
  const [query, setQuery] = useState("");
  const cats = flatten(courses);
  const totalLessons = cats.reduce((n, c) => n + c.count, 0);
  const totalTopics = cats.length;
  const totalCourses = courses.length;
  const featured = !subject && subjects.length > 1 ? featuredBySubject(subjects) : [...cats].sort((a, b) => b.count - a.count).slice(0, 8);
  const firstCourseHref =
    courses[0]?.categories[0] ? `/c/${courses[0].categories[0].code}` : "/";
  const subjectName = subject ? subjectLabel(subject) : null;
  const multi = !subject && subjects.length > 1;
  const knownIds = new Set(subjects.map((s) => s.id));
  const seriesPaths = !subject
    ? SERIES_PATHS.filter((p) => p.ids.every((id) => knownIds.has(id)))
    : [];
  const eyebrow = subjectName
    ? `${subjectName} courses`
    : multi
      ? "Python, LangGraph, AI systems, and engineering"
      : "Python mastery, hands-on";
  const titleLead = subjectName ? `Learn ${subjectName}` : "Learn";
  const titleAccent = subjectName ? "in depth." : "by doing.";
  const lede = subjectName
    ? `${subjectName} on Bestinfopages — ${totalCourses} course${totalCourses === 1 ? "" : "s"}, ${totalTopics} topics, and ${totalLessons.toLocaleString()} lessons.`
    : `Hands-on courses from Python fundamentals to production AI systems and engineering books — ${subjects.length || totalCourses} subjects, ${totalTopics} topics, and ${totalLessons.toLocaleString()} lessons. Read, practice, and learn with an AI tutor on every page.`;
  const seoTitle = subjectName
    ? `Bestinfopages — Learn ${subjectName}`
    : "Bestinfopages — Learn by doing";
  const seoPath = subject ? `/s/${subject}` : "/";

  return (
    <div className="bip-landing">
      <Seo
        title={seoTitle}
        path={seoPath}
        jsonLd={{
          "@context": "https://schema.org",
          "@type": "WebSite",
          name: "Bestinfopages",
          description: lede,
        }}
      />

      <section className="bip-hero" aria-labelledby="home-title">
        <div className="bip-hero__text">
          <span className="bip-hero__eyebrow">{eyebrow}</span>
          <h1 id="home-title" className="bip-hero__title">
            {titleLead}
            {subjectName ? ", " : " "}
            <span className="bip-hero__accent">{titleAccent}</span>
          </h1>
          <p className="bip-hero__lede">{lede}</p>
          <form
            className="bip-hero__search"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              if (query.trim()) navigate(`/search/${encodeURIComponent(query.trim())}`);
            }}
          >
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search 6,000+ lessons…"
              aria-label="Search lessons"
            />
            <button type="submit">Search</button>
          </form>
          <div className="bip-hero__cta">
            {session ? (
              <a className="bip-btn bip-btn--primary" href={firstCourseHref}>
                Continue learning
              </a>
            ) : (
              <a className="bip-btn bip-btn--primary" href="/register">
                Start learning free
              </a>
            )}
            <a className="bip-btn bip-btn--ghost" href={firstCourseHref}>
              Browse courses
            </a>
          </div>
        </div>
        <div className="bip-hero__art">
          <img src={HERO_IMG} alt="" width={1200} height={900} />
        </div>
      </section>

      <section className="bip-stats" aria-label="At a glance">
        <div>
          <strong>{totalLessons.toLocaleString()}</strong>
          <span>Lessons</span>
        </div>
        <div>
          <strong>{totalTopics}</strong>
          <span>Topics</span>
        </div>
        <div>
          <strong>{totalCourses}</strong>
          <span>Courses</span>
        </div>
        <div>
          <strong>Live</strong>
          <span>Code sandbox</span>
        </div>
        <div>
          <strong>AI</strong>
          <span>Tutor on every page</span>
        </div>
      </section>

      <section className="bip-section" aria-labelledby="popular-title">
        <div className="bip-section__head">
          <h2 id="popular-title">{multi ? "Browse by subject" : "Popular topics"}</h2>
          <a href={firstCourseHref}>Browse all →</a>
        </div>
        <div className="bip-cgrid">
          {featured.map((c) => (
            <CourseCard
              key={c.code}
              href={`/c/${c.code}`}
              title={c.title}
              thumb={thumbFor(c.code)}
              count={c.count}
              level={levelForCourse(c.courseIndex, totalCourses)}
              blurb={cleanBlurb(c.summary, c.title)}
            />
          ))}
        </div>
      </section>

      {seriesPaths.length ? (
        <section className="bip-section" aria-labelledby="series-paths-title">
          <div className="bip-section__head">
            <h2 id="series-paths-title">Recommended paths</h2>
            <span className="bip-muted-text">Engineering and agentic systems</span>
          </div>
          <div className="bip-series-paths">
            {seriesPaths.map((p) => (
              <div key={p.title} className="bip-series-path">
                <h3>{p.title}</h3>
                <ol>
                  {p.ids.map((id) => (
                    <li key={id}>
                      <a href={`/s/${id}`}>{subjectLabel(id)}</a>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="bip-section" aria-labelledby="paths-title">
        <div className="bip-section__head">
          <h2 id="paths-title">Learning paths</h2>
          <span className="bip-muted-text">{totalCourses} guided courses</span>
        </div>
        {multi
          ? subjects.map((s) => (
              <div key={s.id} className="bip-subject-block">
                <div className="bip-section__head">
                  <h3 className="bip-subject-block__title">
                    <a href={`/s/${s.id}`}>{s.label}</a>
                  </h3>
                  <a href={`/s/${s.id}`}>Browse {s.label} →</a>
                </div>
                <div className="bip-pathgrid">
                  {s.courses.map((course, i) => pathCard(course, i))}
                </div>
              </div>
            ))
          : (
            <div className="bip-pathgrid">
              {courses.map((course, i) => pathCard(course, i))}
            </div>
          )}
      </section>

      <section className="bip-band" aria-labelledby="how-title">
        <h2 id="how-title">How it works</h2>
        <div className="bip-steps">
          {STEPS.map((s) => (
            <div key={s.n} className="bip-step">
              <span className="bip-step__n">{s.n}</span>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="bip-section" aria-labelledby="why-title">
        <div className="bip-section__head">
          <h2 id="why-title">Why Bestinfopages</h2>
        </div>
        <div className="bip-features">
          {FEATURES.map((f) => (
            <div key={f.title} className="bip-feature">
              <span className="bip-feature__icon">{f.icon}</span>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="bip-section" aria-labelledby="faq-title">
        <div className="bip-section__head">
          <h2 id="faq-title">Frequently asked questions</h2>
        </div>
        <div className="bip-faq">
          {FAQ.map((item) => (
            <details key={item.q} className="bip-faq__item">
              <summary>{item.q}</summary>
              <p>{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      {!session ? (
        <section className="bip-cta-band" aria-label="Get started">
          <h2>Start learning today</h2>
          <p>Create a free account and pick up where curiosity takes you.</p>
          <a className="bip-btn bip-btn--light" href="/register">
            Create your free account
          </a>
        </section>
      ) : null}
    </div>
  );
}
