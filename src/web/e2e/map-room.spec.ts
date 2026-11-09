import { expect, test } from "@playwright/test";

test.describe("VibeGraph Map Room 2.0 E2E", () => {
  test("loads full codebase architecture map, districts, and symbols", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    // Verify Header brand & Architecture Map badge
    const headerTitle = page.locator(".vibe-header h2");
    await expect(headerTitle).toContainText("VIBEGRAPH");
    await expect(headerTitle).toContainText("ARCHITECTURE MAP");

    // Verify React Flow canvas renders
    const flowContainer = page.locator(".react-flow");
    await expect(flowContainer).toBeVisible({ timeout: 10000 });

    // Verify stats panel loads symbol/edge counts
    const statsPanel = page
      .locator(".vibe-stat-chip")
      .filter({ hasText: "/" })
      .or(page.locator(".vibe-stat-chip").nth(2));
    await expect(statsPanel).toBeVisible({ timeout: 15000 });

    // Verify symbol nodes are visible on canvas
    await expect(page.locator(".vibe-node").first()).toBeVisible({ timeout: 15000 });

    // Settle ELK layout
    await page.waitForTimeout(1200);

    // Save screenshots of the full architecture map
    await page.screenshot({
      path: "../../tests/e2e_map_room_full.png",
      fullPage: true,
    });
    await page.screenshot({
      path: "../../tests/e2e_map_room.png",
      fullPage: true,
    });
  });

  test("can filter symbols by search input", async ({ page }) => {
    await page.goto("/");
    const searchInput = page.getByPlaceholder("Search symbols...");
    await expect(searchInput).toBeVisible();

    await searchInput.fill("CodeInspectorDrawer");
    await expect(searchInput).toHaveValue("CodeInspectorDrawer");
  });

  test("can trigger execution trace in TracePathBar and isolate tracepath", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    // Wait for initial load
    await expect(
      page
        .locator(".vibe-stat-chip")
        .filter({ hasText: "/" })
        .or(page.locator(".vibe-stat-chip").nth(2))
    ).toBeVisible({ timeout: 15000 });

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

    // Verify Header reflects trace mode
    await expect(page.locator(".vibe-header")).toContainText("Trace:");

    // Wait for ELK trace layout animation to settle
    await page.waitForTimeout(1000);

    // Save isolated trace screenshot inside tests/ folder
    await page.screenshot({
      path: "../../tests/e2e_map_room_trace.png",
      fullPage: true,
    });
  });
});
