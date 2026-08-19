import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:5173";
const out = process.env.OUT_DIR ?? "/tmp/shots";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 940 }, deviceScaleFactor: 2 });

async function shot(name) {
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/j-${name}.png`, fullPage: false });
  console.log("shot", name);
}

// 1. Home (logged out)
await page.goto(base + "/#/", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
await shot("home");

// 2. Sign in via the mock IdP
await page.getByRole("link", { name: /sign in/i }).first().click();
await page.waitForLoadState("networkidle");
await shot("login");
await page.getByRole("button", { name: /continue/i }).click();
await page.waitForLoadState("networkidle");
await page.waitForTimeout(800);
await shot("home-authed");

// 3. Open the first category from the sidebar
await page.locator(".bip-catcard").first().click();
await page.waitForTimeout(900);
await shot("category");

// 4. Open the first lesson
await page.locator(".bip-sidebar__lesson").first().click().catch(() => {});
await page.waitForTimeout(1000);
await shot("lesson");

// 5. Run a code cell if present
const runBtn = page.getByRole("button", { name: /^run$/i }).first();
if (await runBtn.count()) {
  await runBtn.click();
  await page.waitForTimeout(1500);
  await shot("lesson-run");
}

// 6. Search
await page.locator(".bip-search__input").fill("effects");
await page.locator(".bip-search__input").press("Enter");
await page.waitForTimeout(1200);
await shot("search");

// 7. Activity
await page.goto(base + "/#/activity", { waitUntil: "networkidle" });
await page.waitForTimeout(900);
await shot("activity");

await browser.close();
