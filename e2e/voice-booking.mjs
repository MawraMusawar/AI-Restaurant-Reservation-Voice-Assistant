// A complete phone booking by voice, in real Chrome: the fake microphone plays
// .audio/booking_call.wav (request -> name -> "yes" -> goodbye). Real Deepgram,
// Groq and ElevenLabs; the booking is checked in the database by run.py.
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL;
const WAV = path.resolve(".audio/booking_call.wav");
const results = [];
const check = (name, ok, extra = "") => {
  results.push({ name, ok, extra });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` - ${extra}` : ""}`);
};

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${WAV}`,
    "--autoplay-policy=no-user-gesture-required",
  ],
});
const context = await browser.newContext();
await context.addInitScript(() => {
  window.__rtvi = [];
  const original = RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel = function (...args) {
    const channel = original.apply(this, args);
    channel.addEventListener("message", (e) => {
      try {
        const m = JSON.parse(e.data);
        if (m.label === "rtvi-ai") window.__rtvi.push({ t: Math.round(performance.now()), type: m.type, data: m.data });
      } catch {}
    });
    return channel;
  };
});
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

try {
  await page.goto(`${BASE}/call`, { waitUntil: "networkidle" });
  const t0 = Date.now();
  await page.getByRole("button", { name: "Start call" }).click();
  await page.locator("p[role=status]").filter({ hasText: "On call" }).waitFor({ timeout: 30000 });
  check("call connects", true, `${((Date.now() - t0) / 1000).toFixed(1)} s to "On call"`);

  // The caller says goodbye ~75 s in; the assistant then hangs up by itself.
  await page.getByText("The assistant ended the call").waitFor({ timeout: 150000 });
  check("assistant hangs up after goodbye", true, `${((Date.now() - t0) / 1000).toFixed(0)} s call`);

  const lines = await page.locator('[aria-label="Live transcript"] li').allInnerTexts();
  console.log("TRANSCRIPT:\n  " + lines.join("\n  "));
  const rtvi = await page.evaluate(() => window.__rtvi);
  const tools = rtvi.filter((m) => m.type === "llm-function-call-started").map((m) => m.data?.function_name);
  check("assistant used the booking tools", tools.includes("check_availability") && tools.includes("create_reservation"), tools.join(","));
  fs.writeFileSync(".audio/voice-booking-rtvi.json", JSON.stringify(rtvi, null, 1));
  await page.screenshot({ path: ".audio/voice-booking.png", fullPage: true });
} catch (err) {
  check("unexpected error", false, String(err).split("\n")[0]);
  console.log("TRANSCRIPT:\n  " + (await page.locator('[aria-label="Live transcript"] li').allInnerTexts().catch(() => [])).join("\n  "));
  await page.screenshot({ path: ".audio/voice-booking-failure.png", fullPage: true }).catch(() => {});
} finally {
  check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} browser checks passed`);
  process.exit(failed ? 1 : 0);
}
