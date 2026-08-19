import type { CatalogEntry } from "../api/client";

export interface LessonNode {
  entry: CatalogEntry;
  revisionId: string | null;
  title: string;
  kind: string;
}

// Admin/resource docs get short, clean labels instead of their raw "Category Assessment — …" title.
const KIND_LABELS: Record<string, string> = {
  overview: "Overview",
  project: "Project",
  assessment: "Assessment",
  quiz: "Quiz",
  exercise: "Exercises",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

/** A clean, human title: short label for resource docs, prefix-stripped title for lessons. */
export function displayTitle(entry: CatalogEntry): string {
  const kind = entry.terms.kind?.[0];
  if (kind && KIND_LABELS[kind]) return KIND_LABELS[kind];
  return cleanLessonTitle(entry.title);
}

const CATEGORY_CODE_RE = /^(?:C|LG|RAG|PG|SD|KB|AQA|ARM)\d+\s*[—–-]\s*/i;

/** Strip a leading "Cxx — " (or book-category) code from a category's overview title. */
export function cleanCategoryTitle(title: string): string {
  return title.replace(CATEGORY_CODE_RE, "").trim() || title;
}

export function cleanLessonTitle(title: string): string {
  return (
    title
      .replace(/^PY-[A-Z0-9-]+\s*[:—–-]\s*/i, "")
      .replace(/^C\d+\s*[—–-]\s*/i, "")
      .replace(/^M\d+\s*[—–-]\s*/i, "")
      .replace(/^Lesson\s+\d+\s*[:—–-]\s*/i, "")
      .replace(/^(Category Assessment|Category Project|Exercise Set|Quiz)\s*[—–-]\s*/i, "")
      .trim() || title
  );
}

export interface ModuleGroup {
  module: string;
  label: string;
  lessons: LessonNode[];
}

function firstTerm(entry: CatalogEntry, scheme: string): string | undefined {
  return entry.terms[scheme]?.[0];
}

function orderKey(entry: CatalogEntry): string {
  return firstTerm(entry, "order") ?? "999999";
}

// Pedagogical order of item kinds WITHIN a module/section (not filename order).
const KIND_PRIORITY: Record<string, number> = {
  overview: 0,
  lesson: 1,
  page: 1,
  exercise: 2,
  quiz: 3,
  project: 8,
  assessment: 9,
};

function kindPriority(kind: string): number {
  return KIND_PRIORITY[kind] ?? 5;
}

/** The group a doc belongs to: category Overview first, then modules, then Project/Assessment. */
function groupKeyOf(entry: CatalogEntry): string {
  const module = firstTerm(entry, "module");
  if (module) return module;
  const kind = firstTerm(entry, "kind") ?? "page";
  if (kind === "overview") return "_overview";
  if (kind === "project" || kind === "assessment") return "_capstone";
  return "_category";
}

function groupWeight(key: string): number {
  if (key === "_overview") return -1;
  if (key === "_capstone") return 9999;
  const m = key.match(/^M(\d+)/);
  return m ? Number.parseInt(m[1], 10) : 500;
}

/** Group a category's catalog into ordered sections -> pedagogically-ordered items. */
export function groupByModule(entries: CatalogEntry[]): ModuleGroup[] {
  const groups = new Map<string, ModuleGroup>();
  for (const entry of entries) {
    // The category Overview is the course page itself, not a menu item / lesson, so exclude every
    // "overview" doc from the module tree, the syllabus, and prev/next navigation.
    if (firstTerm(entry, "kind") === "overview") continue;
    const key = groupKeyOf(entry);
    if (!groups.has(key)) {
      groups.set(key, { module: key, label: moduleLabel(key), lessons: [] });
    }
    groups.get(key)!.lessons.push({
      entry,
      revisionId: entry.revision_id,
      title: displayTitle(entry),
      kind: firstTerm(entry, "kind") ?? "page",
    });
  }
  for (const group of groups.values()) {
    group.lessons.sort((a, b) => {
      const byKind = kindPriority(a.kind) - kindPriority(b.kind);
      if (byKind !== 0) return byKind;
      return orderKey(a.entry).localeCompare(orderKey(b.entry));
    });
  }
  return [...groups.values()].sort((a, b) => groupWeight(a.module) - groupWeight(b.module));
}

/** A flat, ordered list of lessons in a category (for prev/next navigation). */
export function flatten(entries: CatalogEntry[]): LessonNode[] {
  return groupByModule(entries).flatMap((g) => g.lessons);
}

export function moduleLabel(module: string): string {
  if (module === "_overview") return "Overview";
  if (module === "_capstone") return "Project & Assessment";
  if (module === "_category") return "Category";
  // "M01-special-method-protocols" -> "Special method protocols" (no code showcased)
  const m = module.match(/^M\d+-(.*)$/);
  if (!m) return module;
  const words = m[1].replace(/-/g, " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

export function categoryLabel(category: string, entries: CatalogEntry[]): string {
  // Prefer the "overview" doc's title if present; else humanise the code.
  const overview = entries.find((e) => (e.terms.kind ?? []).includes("overview"));
  if (overview) return cleanCategoryTitle(overview.title);
  return category;
}

// ---- Course / phase grouping (the curriculum's "multiple courses") ----

export interface CourseCategory {
  code: string;
  title: string;
  count: number;
  summary?: string | null;
}

/** Clean a category summary into a human blurb (strip markdown + templated metadata boilerplate). */
export function cleanBlurb(text: string | null | undefined, fallbackTitle: string): string {
  let s = (text ?? "")
    .replace(/[*`]/g, "")
    .replace(/^\s*Phase:?\s*P?\d*\s*[—–:-]\s*/i, "");
  // Drop the metadata tail the templates append ("... Level: expert Tracks: ml, ai ...").
  s = s.split(/\b(?:Level|Tracks|Track|Prerequisites|Modules)\s*:/i)[0];
  s = s.replace(/\s+/g, " ").trim();
  // Remove a leading duplicate of the title.
  if (s.toLowerCase().startsWith(fallbackTitle.toLowerCase())) {
    s = s.slice(fallbackTitle.length).replace(/^[\s—–:.-]+/, "").trim();
  }
  if (s.length >= 24) return s;
  return `Hands-on lessons and exercises in ${fallbackTitle}.`;
}

/** Rough estimated study time from a lesson count (~12 min per lesson). */
export function estHours(lessons: number): number {
  return Math.max(1, Math.round((lessons * 12) / 60));
}

/** A coarse level label derived from a course's position in the curriculum. */
export function levelForCourse(index: number, total: number): "Beginner" | "Intermediate" | "Advanced" {
  if (total <= 1) return "Beginner";
  const frac = index / (total - 1);
  if (frac < 0.34) return "Beginner";
  if (frac < 0.7) return "Intermediate";
  return "Advanced";
}

/** Up to `max` human skill tags for a category, derived from its module titles. */
export function skillTags(entries: CatalogEntry[], max = 4): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const e of entries) {
    const module = firstTerm(e, "module");
    if (!module) continue;
    const label = moduleLabel(module);
    if (label && !seen.has(label)) {
      seen.add(label);
      tags.push(label);
      if (tags.length >= max) break;
    }
  }
  return tags;
}

export interface Course {
  id: string;
  title: string;
  categories: CourseCategory[];
}

export interface SubjectOutline {
  id: string;
  label: string;
  courses: Course[];
}

// Human labels for the Subject dimension. Keep short — they appear in the header.
const SUBJECT_LABELS: Record<string, string> = {
  python: "Python",
  langgraph: "LangGraph",
  "ai-systems": "AI Systems",
  pytorch: "PyTorch",
  transformers: "Transformers",
  cuda: "CUDA",
  inference: "Inference",
  distributed: "Distributed",
  "ai-mastery": "AI Mastery",
  frontier: "Frontier",
  rag: "RAG",
  postgresql: "PostgreSQL",
  "system-design": "System Design",
  "knowledge-base": "Knowledge Base",
  "agentic-qa": "Agentic QA",
  "agentic-release": "Release Mgmt",
  php: "PHP",
  java: "Java",
  rust: "Rust",
  javascript: "JavaScript",
  go: "Go",
};

/** Header / landing order: language core, then AI-systems split, then the 2026 book series. */
export const SUBJECT_ORDER: string[] = [
  "python",
  "langgraph",
  "ai-systems",
  "pytorch",
  "transformers",
  "cuda",
  "inference",
  "distributed",
  "ai-mastery",
  "frontier",
  "rag",
  "postgresql",
  "system-design",
  "knowledge-base",
  "agentic-qa",
  "agentic-release",
];

export function subjectLabel(id: string): string {
  return SUBJECT_LABELS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

export function subjectRank(id: string): number {
  const i = SUBJECT_ORDER.indexOf(id);
  return i === -1 ? 1000 : i;
}

function phaseNum(p: string): number {
  const m = p.match(/\d+/);
  return m ? Number.parseInt(m[0], 10) : 999;
}

/**
 * Build the subject-aware outline (subject -> course/phase -> category) from the per-category
 * overview docs + counts. Returns the per-subject grouping plus the default subject's courses (for
 * single-subject rendering) and lookup maps.
 */
export function buildOutline(
  overviews: CatalogEntry[],
  counts: Record<string, number>,
): {
  subjects: SubjectOutline[];
  courses: Course[];
  labels: Record<string, string>;
  phaseOf: Record<string, string>;
  subjectOf: Record<string, string>;
} {
  const bySubject = new Map<
    string,
    Map<string, { title: string; num: number; cats: CourseCategory[] }>
  >();
  const labels: Record<string, string> = {};
  const phaseOf: Record<string, string> = {};
  const subjectOf: Record<string, string> = {};
  for (const e of overviews) {
    const cat = e.terms.category?.[0];
    if (!cat || (e.terms.module?.length ?? 0) > 0) continue;
    const subject = e.terms.subject?.[0] ?? "python";
    const title = cleanCategoryTitle(e.title);
    labels[cat] = title;
    subjectOf[cat] = subject;
    const phase = e.terms.phase?.[0] ?? "P999";
    const phaseTitle = e.terms.phase_title?.[0] ?? "Other topics";
    phaseOf[cat] = phase;
    if (!bySubject.has(subject)) bySubject.set(subject, new Map());
    const byPhase = bySubject.get(subject)!;
    if (!byPhase.has(phase)) {
      byPhase.set(phase, { title: phaseTitle, num: phaseNum(phase), cats: [] });
    }
    byPhase
      .get(phase)!
      .cats.push({ code: cat, title, count: counts[cat] ?? 0, summary: e.summary ?? null });
  }
  const subjects: SubjectOutline[] = [...bySubject.entries()]
    .sort((a, b) => {
      const d = subjectRank(a[0]) - subjectRank(b[0]);
      return d !== 0 ? d : a[0].localeCompare(b[0]);
    })
    .map(([id, byPhase]) => ({
      id,
      label: subjectLabel(id),
      courses: [...byPhase.entries()]
        .sort((a, b) => a[1].num - b[1].num)
        .map(([pid, v]) => ({
          id: pid,
          title: v.title,
          categories: v.cats.sort((a, b) => a.code.localeCompare(b.code)),
        })),
    }));
  const python = subjects.find((s) => s.id === "python");
  return {
    subjects,
    courses: python?.courses ?? subjects.flatMap((s) => s.courses),
    labels,
    phaseOf,
    subjectOf,
  };
}
