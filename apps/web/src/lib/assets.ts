// Generated brand imagery (served from apps/web/public/img). A deterministic hash maps each course
// code to one of the topic thumbnails, so every course gets varied art without a per-course file.

export const HERO_IMG = "/img/hero.png";
export const OG_IMG = "/img/og.png";

const THUMB_COUNT = 10;
const THUMBS: string[] = Array.from(
  { length: THUMB_COUNT },
  (_, i) => `/img/thumb-${String(i + 1).padStart(2, "0")}.png`,
);

function hashCode(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** A stable topic thumbnail for a category/course code (e.g. "C07" -> "/img/thumb-04.png"). */
export function thumbFor(code: string): string {
  return THUMBS[hashCode(code || "x") % THUMB_COUNT];
}

const BANNER_COUNT = 36;
const BANNERS: string[] = Array.from(
  { length: BANNER_COUNT },
  (_, i) => `/img/banners/banner-${String(i + 1).padStart(2, "0")}.svg`,
);

/** Curated flagship lessons get unique banner art. */
const FLAGSHIP_BANNERS: Record<string, string> = {
  "PY-C00-M01-L01": "/img/banners/flagship-01.svg",
  "PY-C00-M02-L01": "/img/banners/flagship-02.svg",
  "PY-C01-M01-L01": "/img/banners/flagship-03.svg",
  "PY-C02-M01-L01": "/img/banners/flagship-04.svg",
  "PY-C03-M01-L01": "/img/banners/flagship-05.svg",
  "PY-C05-M01-L01": "/img/banners/flagship-06.svg",
  "PY-C10-M01-L01": "/img/banners/flagship-07.svg",
  "PY-C20-M01-L01": "/img/banners/flagship-08.svg",
};

/** Deterministic wide banner for a module (category + module code). */
export function bannerForModule(category: string, module: string): string {
  const key = `${category}:${module || "_root"}`;
  return BANNERS[hashCode(key) % BANNER_COUNT];
}

/** Banner for a lesson — flagship override, else module banner. */
export function bannerForLesson(
  lessonId: string | null | undefined,
  category: string,
  module: string,
): string {
  if (lessonId) {
    const flagship = FLAGSHIP_BANNERS[lessonId.toUpperCase()];
    if (flagship) return flagship;
  }
  return bannerForModule(category, module);
}
