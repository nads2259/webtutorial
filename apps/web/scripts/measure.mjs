import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
for (const hash of ["", "#/authoring"]) {
  await p.goto("http://localhost:5173/" + hash, { waitUntil: "networkidle" });
  await p.waitForTimeout(300);
  const data = await p.evaluate(() => {
    const header = document.querySelector(".ns-header");
    const main = document.querySelector(".ns-main");
    const kicker = document.querySelector(".bip-kicker");
    const r = (el) => el ? el.getBoundingClientRect() : null;
    const cs = getComputedStyle(document.querySelector(".ns-shell"));
    return {
      shellDisplay: cs.display,
      header: r(header) && { top: r(header).top, bottom: r(header).bottom, pos: getComputedStyle(header).position },
      main: r(main) && { top: r(main).top, paddingTop: getComputedStyle(main).paddingTop },
      kicker: r(kicker) && { top: r(kicker).top },
    };
  });
  console.log(hash || "reader", JSON.stringify(data));
}
await b.close();
