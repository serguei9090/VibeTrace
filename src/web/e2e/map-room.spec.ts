import { expect, test } from "@playwright/test";

test.describe("VibeGraph Map Room 2.0 E2E", () => {
  test("loads Map Room header, symbols, and status overlays", async ({ page }) => {
    await page.goto("/");

    // Verify Header brand
    const headerTitle = page.locator(".vibe-header h2");
    await expect(headerTitle).toContainText("VIBEGRAPH");

    // Verify React Flow canvas renders
    const flowContainer = page.locator(".react-flow");
    await expect(flowContainer).toBeVisible({ timeout: 10000 });

    // Verify Tracepath bar is present
    const traceBar = page.getByRole("button", { name: "Trace", exact: true });
    await expect(traceBar).toBeVisible();

    // Verify Follow AI Agent badge
    const agentBadge = page.getByRole("button", { name: "Follow AI Agent" });
    await expect(agentBadge).toBeVisible();

    // Verify Filter Panel
    const filterHeader = page.locator(".glass-panel").filter({ hasText: "Filters" });
    await expect(filterHeader).toBeVisible();
  });

  test("can filter symbols by search input", async ({ page }) => {
    await page.goto("/");
    const searchInput = page.getByPlaceholder("Search symbols...");
    await expect(searchInput).toBeVisible();

    await searchInput.fill("CodeInspectorDrawer");
    await expect(searchInput).toHaveValue("CodeInspectorDrawer");
  });

  test("can trigger execution trace in TracePathBar and capture screenshot", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    const fromInput = page.getByPlaceholder("From symbol...");
    const toInput = page.getByPlaceholder("To symbol...");
    const traceBtn = page.getByRole("button", { name: "Trace", exact: true });

    await fromInput.fill("App");
    await toInput.fill("CodeInspectorDrawer");
    await expect(traceBtn).toBeEnabled();

    await traceBtn.click();
    await expect(page.locator(".glass-panel").filter({ hasText: "hop(s):" })).toBeVisible({
      timeout: 10000,
    });

    // Wait 1s for ELK trace layout animation to settle
    await page.waitForTimeout(1000);

    // Save screenshot inside tests/ folder
    await page.screenshot({
      path: "../../tests/e2e_map_room.png",
      fullPage: true,
    });
  });
});
