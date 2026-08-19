import type { Course, ModuleGroup } from "../lib/tree";

export interface CategorySidebarProps {
  courses: Course[];
  activeCategory: string | null;
  activeRevisionId: string | null;
  groups: ModuleGroup[];
  labelFor: (code: string) => string;
  backHref?: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Left navigation grouped as courses (phases) -> categories -> modules -> lessons. The active
 * category expands to reveal its module/lesson tree. Rendered as a labelled navigation landmark; on
 * narrow viewports it is an off-canvas drawer toggled by the header menu button.
 */
export function CategorySidebar({
  courses,
  activeCategory,
  activeRevisionId,
  groups,
  labelFor,
  backHref = "/",
  open,
  onClose,
}: CategorySidebarProps): React.JSX.Element {
  // Once a course is selected, collapse the menu to just that course.
  const activeCourse = activeCategory
    ? courses.find((c) => c.categories.some((cat) => cat.code === activeCategory))
    : null;
  const shown = activeCourse ? [activeCourse] : courses;
  const numberOf = (id: string): number => courses.findIndex((c) => c.id === id) + 1;

  return (
    <nav
      className={`bip-sidebar${open ? " bip-sidebar--open" : ""}`}
      aria-label="Lessons"
      id="lessons-nav"
    >
      <div className="bip-sidebar__head">
        {activeCourse ? (
          <a className="bip-sidebar__back" href={backHref}>
            ← All courses
          </a>
        ) : (
          <span className="bip-sidebar__title">Curriculum</span>
        )}
        <button type="button" className="bip-sidebar__close" onClick={onClose} aria-label="Close menu">
          ✕
        </button>
      </div>

      {shown.map((course) => (
        <div key={course.id} className="bip-sidebar__course">
          <div className="bip-sidebar__coursehead">
            <span className="bip-sidebar__coursenum">Course {numberOf(course.id)}</span>
            {course.title}
          </div>
          <ul className="bip-sidebar__cats">
            {course.categories.map((cat) => {
              const isActive = cat.code === activeCategory;
              return (
                <li key={cat.code} className="bip-sidebar__cat">
                  <a
                    href={`/c/${cat.code}`}
                    className={`bip-sidebar__catlink${isActive ? " is-active" : ""}`}
                    aria-current={isActive ? "true" : undefined}
                    title={labelFor(cat.code)}
                  >
                    <span className="bip-sidebar__catname">{labelFor(cat.code)}</span>
                    <span className="bip-sidebar__count">{cat.count}</span>
                  </a>
                  {isActive ? (
                    <div className="bip-sidebar__tree">
                      {groups.map((group) => (
                        <div key={group.module} className="bip-sidebar__module">
                          {group.lessons.length > 1 ? (
                            <div className="bip-sidebar__modlabel">{group.label}</div>
                          ) : null}
                          <ul>
                            {group.lessons.map((lesson) => (
                              <li key={lesson.entry.object_id}>
                                <a
                                  href={`/l/${cat.code}/${lesson.revisionId}`}
                                  className={`bip-sidebar__lesson${
                                    lesson.revisionId === activeRevisionId ? " is-active" : ""
                                  }`}
                                  aria-current={
                                    lesson.revisionId === activeRevisionId ? "page" : undefined
                                  }
                                >
                                  {lesson.title}
                                </a>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
