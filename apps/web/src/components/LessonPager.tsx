import type { LessonNode } from "../lib/tree";

export interface LessonPagerProps {
  category: string;
  prev: LessonNode | null;
  next: LessonNode | null;
}

/** Prev/next lesson navigation at the foot of a lesson (keyboard + history friendly links). */
export function LessonPager({ category, prev, next }: LessonPagerProps): React.JSX.Element {
  return (
    <nav className="bip-pager" aria-label="Lesson navigation">
      {prev ? (
        <a className="bip-pager__link bip-pager__prev" href={`/l/${category}/${prev.revisionId}`}>
          <span className="bip-pager__dir">← Previous</span>
          <span className="bip-pager__title">{prev.title}</span>
        </a>
      ) : (
        <span />
      )}
      {next ? (
        <a className="bip-pager__link bip-pager__next" href={`/l/${category}/${next.revisionId}`}>
          <span className="bip-pager__dir">Next →</span>
          <span className="bip-pager__title">{next.title}</span>
        </a>
      ) : (
        <span />
      )}
    </nav>
  );
}
