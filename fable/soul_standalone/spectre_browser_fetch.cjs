"use strict";

const fs = require("fs");

async function main() {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const { chromium } = require(input.playwright_core);
  const allowedOrigin = new URL(input.allowed_origin).origin;
  const browser = await chromium.launch({
    headless: true,
    executablePath: input.chromium,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const context = await browser.newContext({
      javaScriptEnabled: true,
      acceptDownloads: false,
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    await page.route("**/*", async (route) => {
      let target;
      try {
        target = new URL(route.request().url());
      } catch {
        return route.abort("blockedbyclient");
      }
      if (target.origin !== allowedOrigin) return route.abort("blockedbyclient");
      const method = route.request().method();
      if (method !== "GET" && method !== "HEAD") return route.abort("blockedbyclient");
      return route.continue();
    });
    await page.goto(input.url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(400);
    const result = await page.evaluate(({ origin, maxChars }) => {
      const links = [];
      const seen = new Set();
      for (const anchor of document.querySelectorAll("a[href]")) {
        try {
          const url = new URL(anchor.href, location.href);
          if (url.origin === origin && !seen.has(url.href)) {
            seen.add(url.href);
            links.push(url.href);
          }
          if (links.length >= 100) break;
        } catch {}
      }
      return {
        title: document.title || "",
        text: (document.body?.innerText || "").slice(0, maxChars),
        links,
      };
    }, { origin: allowedOrigin, maxChars: input.max_chars });
    const finalUrl = page.url();
    if (new URL(finalUrl).origin !== allowedOrigin) throw new Error("navigation escaped allowed origin");
    process.stdout.write(JSON.stringify({ ...result, final_url: finalUrl }));
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exit(1);
});
