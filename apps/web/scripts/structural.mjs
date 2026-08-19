/** Structural IA checks for the curriculum redesign (path-based routing). */
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:5173";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 940 } });

let failed = 0;
function assert(name, ok) {
  if (!ok) {
    console.error("FAIL:", name);
    failed += 1;
  } else {
    console.log("ok:", name);
  }
}

// Home: full-width, no sidebar Lessons nav
await page.goto(`${base}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
assert("home has no sidebar Lessons nav", (await page.getByRole("navigation", { name: /lessons/i }).count()) === 0);
assert("home has hero/landing", (await page.locator(".bip-hero, .bip-section").count()) > 0);

// Category route: sidebar visible with Lessons nav
await page.goto(`${base}/c/C00`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
assert("category has Lessons sidebar", (await page.getByRole("navigation", { name: /lessons/i }).count()) > 0);

// Open first lesson if available
const lessonLink = page.locator(".bip-sidebar__lesson").first();
if (await lessonLink.count()) {
  await lessonLink.click();
  await page.waitForTimeout(1000);
  assert("lesson has no decorative banner", (await page.locator(".bip-lesson-banner").count()) === 0);
  assert("lesson has h1 title", (await page.locator("#lesson-title").count()) === 1);
  // Generic gen-diagram blocks should not dominate (no mermaid immediately under title area)
  const mermaidCount = await page.locator(".bip-diagram, .mermaid").count();
  assert("lesson has at most one diagram", mermaidCount <= 1);
}

// LangGraph subject: filtered landing, LG00 tree, display-only python, rewritten next-lesson link
await page.goto(`${base}/s/langgraph`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
assert("langgraph landing h1", /LangGraph/i.test(await page.locator("h1").innerText()));
assert(
  "langgraph landing has no Lessons sidebar",
  (await page.getByRole("navigation", { name: /lessons/i }).count()) === 0,
);

await page.goto(`${base}/c/LG00`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
assert("LG00 has Lessons sidebar", (await page.getByRole("navigation", { name: /lessons/i }).count()) > 0);
assert("LG00 lists 11+ topics once", (await page.locator(".bip-sidebar__lesson").count()) >= 11);
assert("LG00 does not repeat module headings", (await page.locator(".bip-sidebar__modlabel").count()) === 0);

const lgLesson = page.locator(".bip-sidebar__lesson").first();
if (await lgLesson.count()) {
  await lgLesson.click();
  await page.waitForTimeout(1500);
  assert("langgraph lesson has no decorative banner", (await page.locator(".bip-lesson-banner").count()) === 0);
  assert("langgraph lesson diagram", (await page.locator(".bip-diagram, svg").count()) >= 1);
  assert(
    "langgraph python is display-only",
    (await page.getByRole("button", { name: /^run$/i }).count()) === 0,
  );
  const next = page.locator(".bip-article").getByRole("link", { name: /Lesson 2/i }).first();
  if (await next.count()) {
    await next.click();
    await page.waitForTimeout(1200);
    assert("next chapter is Lesson 2", /State Schemas/i.test(await page.locator("#lesson-title").innerText()));
  }
}

await browser.close();
process.exit(failed ? 1 : 0);
