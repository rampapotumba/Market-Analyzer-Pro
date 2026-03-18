import { test, expect } from "@playwright/test";

/**
 * Navigation smoke tests — verify each page loads without JS errors
 * and renders its heading.
 * These run against the Next.js dev/prod server with the API mocked
 * at the network level so no backend is required.
 */

const PAGES = [
  { path: "/", heading: "Dashboard" },
  { path: "/signals", heading: "Signals" },
  { path: "/instruments", heading: "Instruments" },
  { path: "/portfolio", heading: "Portfolio" },
  { path: "/backtests", heading: "Backtests" },
  { path: "/macro", heading: "Macro Data" },
  { path: "/accuracy", heading: "Signal Accuracy" },
  { path: "/settings", heading: "Settings" },
];

test.describe("Page navigation", () => {
  for (const { path, heading } of PAGES) {
    test(`${path} renders heading "${heading}"`, async ({ page }) => {
      // Intercept all API calls so the test is backend-independent.
      await page.route("**/api/**", (route) => {
        // Return minimal valid payloads so pages render without errors.
        const url = route.request().url();
        if (url.includes("/signals")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/instruments")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/portfolio/heat")) {
          route.fulfill({ json: { heat_pct: 0 } });
        } else if (url.includes("/portfolio")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/backtests")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/macro")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/regime")) {
          route.fulfill({ json: [] });
        } else if (url.includes("/accuracy")) {
          route.fulfill({ json: [] });
        } else {
          route.fulfill({ json: {} });
        }
      });

      await page.goto(path);

      // Wait for the main heading to appear
      const h1 = page.locator("h1").first();
      await expect(h1).toBeVisible({ timeout: 10_000 });
      await expect(h1).toContainText(heading);
    });
  }
});

test.describe("Navbar", () => {
  test("all nav links are present on the dashboard", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/v2/portfolio/heat", (route) =>
      route.fulfill({ json: { heat_pct: 0 } })
    );
    await page.route("**/api/v2/regime", (route) => route.fulfill({ json: [] }));

    await page.goto("/");

    const nav = page.locator("nav");
    await expect(nav).toBeVisible();

    const expectedLinks = [
      "Dashboard",
      "Signals",
      "Instruments",
      "Portfolio",
      "Backtests",
      "Macro",
      "Accuracy",
      "Settings",
    ];

    for (const label of expectedLinks) {
      await expect(nav.getByText(label)).toBeVisible();
    }
  });

  test("active link is highlighted on signals page", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ json: [] }));

    await page.goto("/signals");

    // The active nav link has bg-gray-100 class
    const activeLink = page.locator("nav a.bg-gray-100");
    await expect(activeLink).toBeVisible();
    await expect(activeLink).toContainText("Signals");
  });
});

test.describe("Signals page", () => {
  test("shows empty state when no signals", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ json: [] }));

    await page.goto("/signals");
    await expect(page.getByText("No signals found")).toBeVisible({ timeout: 10_000 });
  });

  test("renders signal rows when data is returned", async ({ page }) => {
    const mockSignal = {
      id: 1,
      instrument_id: 1,
      symbol: "EURUSD",
      timeframe: "H1",
      direction: "LONG",
      composite_score: 72.5,
      confidence: 0.8,
      strength: "STRONG",
      entry_price: 1.08512,
      stop_loss: 1.08100,
      take_profit_1: 1.09000,
      risk_reward: 2.1,
      regime: "TREND_BULL",
      status: "ACTIVE",
      created_at: new Date().toISOString(),
    };

    await page.route("**/api/v2/signals*", (route) =>
      route.fulfill({ json: [mockSignal] })
    );

    await page.goto("/signals");

    await expect(page.getByText("EURUSD")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("LONG")).toBeVisible();
    await expect(page.getByText("H1")).toBeVisible();
  });
});

test.describe("Instruments page", () => {
  test("search filter narrows results", async ({ page }) => {
    const mockInstruments = [
      { id: 1, symbol: "EURUSD", name: "Euro / US Dollar", type: "forex", is_active: true },
      { id: 2, symbol: "BTCUSD", name: "Bitcoin / US Dollar", type: "crypto", is_active: true },
      { id: 3, symbol: "AAPL", name: "Apple Inc.", type: "stock", is_active: true },
    ];

    await page.route("**/api/v2/instruments", (route) =>
      route.fulfill({ json: mockInstruments })
    );

    await page.goto("/instruments");

    // All 3 should be visible initially
    await expect(page.getByText("EURUSD")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("BTCUSD")).toBeVisible();
    await expect(page.getByText("AAPL")).toBeVisible();

    // Filter by "BTC"
    await page.getByPlaceholder("Search symbol or name...").fill("BTC");
    await expect(page.getByText("BTCUSD")).toBeVisible();
    await expect(page.getByText("EURUSD")).not.toBeVisible();
    await expect(page.getByText("AAPL")).not.toBeVisible();
  });
});

test.describe("Settings page", () => {
  test("save button shows confirmation", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Settings")).toBeVisible();
    await page.getByRole("button", { name: "Save Settings" }).click();
    await expect(page.getByText("Settings saved!")).toBeVisible({ timeout: 3_000 });
  });
});
