// Admin screens E2E (Milestone 7): login, dashboard, reservations (create, full slot ->
// alternative, cancel), call logs, settings. Runs against test servers set up by run_admin_e2e.py.
import { chromium } from "playwright-core";
import path from "node:path";

const BASE = process.env.BASE_URL;
const shot = (name) => path.resolve(`.audio/admin-${name}.png`);
const results = [];
const check = (name, ok, extra = "") => {
  results.push({ name, ok, extra });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` - ${extra}` : ""}`);
};

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true });
const page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("dialog", (d) => d.accept()); // confirm() before cancelling

try {
  // 1. Protected screens redirect to login; wrong password is rejected.
  await page.goto(`${BASE}/reservations`);
  await page.waitForURL("**/login", { timeout: 15000 });
  check("redirects to login", page.url().endsWith("/login"));
  await page.getByLabel("Admin password").fill("wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Wrong password.").waitFor({ timeout: 10000 });
  check("wrong password rejected", true);
  consoleErrors.length = 0; // that 401 was deliberate
  await page.getByLabel("Admin password").fill(process.env.PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  // 2. Dashboard.
  await page.getByRole("heading", { name: "Dashboard" }).waitFor({ timeout: 15000 });
  await page.getByText("E2E Bistro").first().waitFor();
  check("dashboard loads", (await page.getByText("Today's reservations").count()) === 1);
  check("dashboard shows the stored call", (await page.getByText("Do you have a table for two tomorrow evening?").count()) > 0);
  await page.screenshot({ path: shot("dashboard"), fullPage: true });

  // 3. Reservations: create two bookings for the same slot (fills both tables).
  await page.getByRole("link", { name: "Reservations", exact: true }).click();
  await page.getByRole("heading", { name: "Reservations" }).waitFor();
  const book = async (name) => {
    await page.getByRole("button", { name: "New reservation" }).click();
    await page.getByLabel("Customer name", { exact: true }).fill(name);
    await page.getByLabel("Guests", { exact: true }).fill("2");
    await page.getByLabel("Time", { exact: true }).fill("19:00");
    await page.getByRole("button", { name: "Book table" }).click();
  };
  await book("Hina Khan");
  const booked = page.getByText(/^Booked EE-[A-Z0-9]{6}/);
  await booked.waitFor({ timeout: 15000 });
  const firstRef = (await booked.innerText()).match(/EE-[A-Z0-9]{6}/)[0];
  check("staff booking created", true, firstRef);
  await book("Ali Raza");
  await page.getByText(/^Booked EE-.*Ali Raza/).waitFor({ timeout: 15000 });

  // Third booking for the same slot: full -> alternatives offered -> pick one.
  await book("Sara Ahmed");
  const taken = page.getByText("That time was just taken");
  await taken.waitFor({ timeout: 15000 });
  const alt = page.locator("button", { hasText: /PM$/ }).first();
  const altTime = await alt.innerText();
  check("full slot offers alternatives", true, `first: ${altTime}`);
  await alt.click();
  await page.getByRole("button", { name: "Book table" }).click();
  await page.getByText(/^Booked EE-.*Sara Ahmed/).waitFor({ timeout: 15000 });
  check("booking an alternative works", true);
  await page.locator("tr", { hasText: "Sara Ahmed" }).waitFor({ timeout: 15000 });
  check("new booking appears in the list", true);
  await page.screenshot({ path: shot("reservations"), fullPage: true });

  // Cancel the first booking.
  await page.getByRole("button", { name: `Cancel ${firstRef}` }).click();
  await page.getByText(`Cancelled ${firstRef}`).waitFor({ timeout: 15000 });
  const row = page.locator("tr", { hasText: firstRef });
  await row.getByText("cancelled").waitFor({ timeout: 15000 });
  check("cancel works", true);

  // 4. Call logs.
  await page.getByRole("link", { name: "Call logs", exact: true }).click();
  await page.getByRole("heading", { name: "Call logs" }).waitFor();
  await page.getByRole("button", { name: /Do you have a table for two/ }).click();
  await page.getByText("Yes, we have 7 PM available. May I have your name?").waitFor({ timeout: 15000 });
  check("call transcript opens", true);
  const current = await page.locator('nav[aria-label="Main"] [aria-current="page"]').allInnerTexts();
  check("only Call logs is highlighted", current.length === 1 && current[0].includes("Call logs"), current.join(","));
  await page.screenshot({ path: shot("calls"), fullPage: true });

  // 5. Settings: hours, tables, assistant.
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("heading", { name: "Settings" }).waitFor();
  await page.getByLabel("Name", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "Opening hours" }).click();
  await page.locator("label", { hasText: "Open" }).first().locator("input").uncheck();
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Saved.").waitFor({ timeout: 15000 });
  check("hours saved (Monday closed)", (await page.getByText("Closed").count()) >= 1);

  await page.getByRole("tab", { name: "Tables" }).click();
  await page.getByLabel("New table", { exact: true }).fill("T9");
  await page.getByLabel("Seats", { exact: true }).fill("6");
  await page.getByRole("button", { name: "Add table" }).click();
  await page.getByRole("cell", { name: "T9", exact: true }).waitFor({ timeout: 15000 });
  check("table added", true);
  await page.getByLabel("New table", { exact: true }).fill("T9");
  await page.getByRole("button", { name: "Add table" }).click();
  await page.getByText("That already exists.").waitFor({ timeout: 15000 });
  check("duplicate table rejected", true);
  await page.screenshot({ path: shot("settings-tables"), fullPage: true });

  await page.getByRole("tab", { name: "Assistant" }).click();
  await page.getByRole("textbox", { name: /^Personality/ }).fill("cheerful and brief");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Saved.").waitFor({ timeout: 15000 });
  check("assistant settings saved", true);

  // 6. Dashboard reflects the bookings.
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await page.getByText("Total reservations").waitFor();
  const total = await page.locator("p", { hasText: "Total reservations" }).locator("..").locator("p").nth(1).innerText();
  check("dashboard counts bookings", total.trim() === "2", `total=${total}`);
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL("**/login", { timeout: 10000 });
  check("sign out", true);

  // 7. Small screen: navigation still usable.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/call`);
  await page.getByRole("heading", { name: "Live call" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check("mobile: no horizontal page scroll on /call", !overflow);
  await page.screenshot({ path: shot("mobile-call"), fullPage: true });
} catch (err) {
  check("unexpected error", false, String(err).split("\n")[0]);
  await page.screenshot({ path: shot("failure"), fullPage: true }).catch(() => {});
} finally {
  // The full slot and the duplicate table return 409 on purpose; the browser logs those.
  const unexpected = consoleErrors.filter((e) => !/status of 409/.test(e));
  check("no console errors", unexpected.length === 0, unexpected.slice(0, 3).join(" | "));
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}
