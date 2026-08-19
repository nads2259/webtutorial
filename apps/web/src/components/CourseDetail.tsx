import type { CatalogEntry } from "../api/client";
import { thumbFor } from "../lib/assets";
import {
  type LessonNode,
  type ModuleGroup,
  cleanBlurb,
  estHours,
  flatten,
  skillTags,
  subjectLabel,
} from "../lib/tree";
import { Seo } from "./Seo";

export interface CourseDetailProps {
  code: string;
  title: string;
  catalog: CatalogEntry[];
  groups: ModuleGroup[];
  level: string;
  subject?: string;
}

function overviewBlurb(catalog: CatalogEntry[], title: string): string {
  const overview = catalog.find((e) => (e.terms.kind ?? []).includes("overview"));
  return cleanBlurb(overview?.summary, title);
}

function SyllabusItem({ code, lesson }: { code: string; lesson: LessonNode }): React.JSX.Element {
  return (
    <li>
      <a href={`/l/${code}/${lesson.revisionId}`} className="bip-syl__item">
        <span className="bip-syl__itemtitle">{lesson.title}</span>
        {lesson.kind !== "lesson" && lesson.kind !== "page" ? (
          <span className="bip-kind-badge">{lesson.kind}</span>
        ) : null}
      </a>
    </li>
  );
}

export function CourseDetail({
  code,
  title,
  catalog,
  groups,
  level,
  subject,
}: CourseDetailProps): React.JSX.Element {
  const lessons = flatten(catalog);
  const first = lessons[0] ?? null;
  const startHref = first?.revisionId ? `/l/${code}/${first.revisionId}` : null;
  const moduleCount = groups.filter((g) => g.module.startsWith("M")).length || groups.length;
  const learn = skillTags(catalog, 8);
  const blurb = overviewBlurb(catalog, title);

  return (
    <div className="bip-detail">
      <Seo
        title={title}
        description={blurb.slice(0, 300)}
        path={`/c/${code}`}
        jsonLd={{
          "@context": "https://schema.org",
          "@type": "Course",
          name: title,
          description: blurb.slice(0, 300),
        }}
      />
      <nav className="bip-crumbs" aria-label="Breadcrumb">
        {subject ? (
          <>
            <a href={`/s/${subject}`}>{subjectLabel(subject)}</a>
            <span aria-hidden="true">/</span>
          </>
        ) : null}
        <span aria-current="page">{title}</span>
      </nav>

      <header className="bip-detail__hero">
        <div className="bip-detail__intro">
          <span className="bip-chip">{level}</span>
          <h1 id="cat-title">{title}</h1>
          <p className="bip-detail__blurb">{blurb}</p>
          <div className="bip-detail__stats">
            <span>
              <strong>{catalog.length}</strong> items
            </span>
            <span>
              <strong>{moduleCount}</strong> modules
            </span>
            <span>
              <strong>~{estHours(catalog.length)}h</strong> to complete
            </span>
          </div>
          <div className="bip-detail__actions">
            {startHref ? (
              <a className="bip-btn bip-btn--primary" href={startHref}>
                Start course
              </a>
            ) : null}
          </div>
        </div>
        <div className="bip-detail__art">
          <img src={thumbFor(code)} alt="" loading="lazy" />
        </div>
      </header>

      {learn.length > 0 ? (
        <section className="bip-detail__learn" aria-labelledby="learn-title">
          <h2 id="learn-title">What you'll learn</h2>
          <ul>
            {learn.map((l) => (
              <li key={l}>
                <span className="bip-check" aria-hidden="true">
                  ✓
                </span>
                {l}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="bip-detail__syllabus" aria-labelledby="syllabus-title">
        <h2 id="syllabus-title">Syllabus</h2>
        {groups.every((g) => g.lessons.length === 1) ? (
          <ol className="bip-syl__items bip-syl__items--flat">
            {groups.flatMap((g) => g.lessons).map((lesson) => (
              <SyllabusItem key={lesson.entry.object_id} code={code} lesson={lesson} />
            ))}
          </ol>
        ) : (
          groups.map((group, gi) => (
            <details key={group.module} className="bip-syl" open={gi < 2}>
              <summary>
                <span className="bip-syl__label">{group.label}</span>
                <span className="bip-syl__count">{group.lessons.length} items</span>
              </summary>
              <ol className="bip-syl__items">
                {group.lessons.map((lesson) => (
                  <SyllabusItem key={lesson.entry.object_id} code={code} lesson={lesson} />
                ))}
              </ol>
            </details>
          ))
        )}
      </section>
    </div>
  );
}
