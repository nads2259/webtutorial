import { estHours } from "../lib/tree";

export interface CourseCardProps {
  href: string;
  title: string;
  thumb: string;
  count: number;
  level?: string;
  blurb?: string | null;
  tags?: string[];
}

/** A Coursera-style course tile: topic art, level, title, blurb, skills and study stats. */
export function CourseCard({
  href,
  title,
  thumb,
  count,
  level,
  blurb,
  tags,
}: CourseCardProps): React.JSX.Element {
  return (
    <a className="bip-ccard" href={href}>
      <div className="bip-ccard__media">
        <img src={thumb} alt="" loading="lazy" />
        {level ? <span className="bip-ccard__level">{level}</span> : null}
      </div>
      <div className="bip-ccard__body">
        <h3 className="bip-ccard__title">{title}</h3>
        {blurb ? <p className="bip-ccard__blurb">{blurb}</p> : null}
        {tags && tags.length > 0 ? (
          <div className="bip-ccard__tags">
            {tags.slice(0, 3).map((t) => (
              <span key={t} className="bip-ccard__tag">
                {t}
              </span>
            ))}
          </div>
        ) : null}
        <div className="bip-ccard__foot">
          <span>{count} lessons</span>
          <span className="bip-dot" aria-hidden="true" />
          <span>~{estHours(count)}h</span>
        </div>
      </div>
    </a>
  );
}
