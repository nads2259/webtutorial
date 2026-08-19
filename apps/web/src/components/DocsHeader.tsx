import { useState } from "react";
import { type Session } from "../api/client";
import { type SubjectOutline } from "../lib/tree";

export interface DocsHeaderProps {
  session: Session | null;
  subjects?: SubjectOutline[];
  activeSubject?: string;
  showMenuButton?: boolean;
  initialQuery: string;
  onSearch: (q: string) => void;
  onToggleMenu: () => void;
  onLogout: () => void;
}

function SubjectLink({
  subject,
  active,
}: {
  subject: SubjectOutline;
  active: boolean;
}): React.JSX.Element {
  return (
    <a href={`/s/${subject.id}`} aria-current={active ? "page" : undefined}>
      {subject.label}
    </a>
  );
}

/**
 * The banner: wordmark + search + account on the first row, every subject as a header
 * link underneath (wrapping onto a second row when needed — no overflow menu).
 */
export function DocsHeader({
  session,
  subjects,
  activeSubject,
  showMenuButton = true,
  initialQuery,
  onSearch,
  onToggleMenu,
  onLogout,
}: DocsHeaderProps): React.JSX.Element {
  const [query, setQuery] = useState(initialQuery);

  return (
    <header className="ns-header bip-docs-header">
      <div className="bip-docs-header__inner">
        <div className="bip-docs-header__top">
          <div className="bip-docs-header__left">
            {showMenuButton ? (
              <button
                type="button"
                className="bip-menu-btn"
                onClick={onToggleMenu}
                aria-label="Toggle lessons menu"
                aria-controls="lessons-nav"
              >
                ☰
              </button>
            ) : null}
            <a className="bip-wordmark" href="/">
              Bestinfopages
            </a>
          </div>

          <form
            className="bip-search"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              onSearch(query.trim());
            }}
          >
            <span className="bip-search__icon" aria-hidden="true">
              ⌕
            </span>
            <input
              type="search"
              className="bip-search__input"
              placeholder="Search lessons…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search lessons"
            />
          </form>

          <div className="bip-docs-header__right">
            {session ? (
              <a href="/activity">Activity</a>
            ) : null}
            {session?.is_admin ? (
              <a href="/admin">Manage</a>
            ) : null}
            {session ? (
              <div className="bip-user">
                <span className="bip-user__name" title={session.subject_id}>
                  {session.tenant_scope ?? "signed in"}
                </span>
                <button type="button" className="bip-user__btn" onClick={onLogout}>
                  Sign out
                </button>
              </div>
            ) : (
              <a className="bip-cta" href="/login">
                Sign in
              </a>
            )}
          </div>
        </div>

        <nav className="bip-docs-header__subjects" aria-label="Primary">
          <ul>
            {(subjects ?? []).map((s) => (
              <li key={s.id}>
                <SubjectLink subject={s} active={activeSubject === s.id} />
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
