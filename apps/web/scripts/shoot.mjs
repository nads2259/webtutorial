import { chromium } from "playwright";

const routes = [
  { hash: "", name: "reader" },
  { hash: "#/authoring", name: "authoring" },
  { hash: "#/simulation", name: "simulation" },
];
const base = process.env.BASE_URL ?? "http://localhost:5173/";
const outDir = process.env.OUT_DIR ?? "/tmp/shots";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
for (const r of routes) {
  await page.goto(base + r.hash, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${outDir}/${r.name}.png`, fullPage: true });
  await page.screenshot({ path: `${outDir}/${r.name}-fold.png`, fullPage: false });
  console.log(`shot ${r.name}`);
}
await browser.close();
