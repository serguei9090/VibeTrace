import { expect, test } from "@playwright/test";

const finding = (filePath: string) => ({
  id: "SEC-CWE-78-abc",
  rule_id: "py:CWE-78",
  cwe_id: "CWE-78",
  category: "taint_reachability",
  severity: "CRITICAL",
  title: "OS Command Injection",
  message: "Direct os.system call.",
  file_path: filePath,
  start_line: 1,
  end_line: 100000,
  code_hash: "deadbeef",
  non_compliant_snippet: "os.system(x)",
  compliant_snippet: "subprocess.run([x])",
  remediation_guidance: "Avoid the shell.",
  status: "UNAPPROVED",
  metadata: { taint_path: ["handler", "sink"] },
});

test.describe("Security audit card", () => {
  test("shows taint path, copies fix prompt, and approves an exception", async ({ page }) => {
    let approvePayload: Record<string, unknown> | null = null;

    await page.route("**/api/security/audit**", async (route) => {
      const filePath = new URL(route.request().url()).searchParams.get("file_path") ?? "x.py";
      await route.fulfill({
        json: {
          total_findings: 1,
          critical_count: 1,
          high_count: 0,
          medium_count: 0,
          low_count: 0,
          unapproved_count: 1,
          modified_count: 0,
          approved_count: 0,
          findings: [finding(filePath)],
        },
      });
    });
    await page.route("**/api/security/approve", async (route) => {
      approvePayload = route.request().postDataJSON();
      await route.fulfill({ json: { ok: true } });
    });

    let traceUrl = "";
    await page.route("**/trace?**", async (route) => {
      traceUrl = route.request().url();
      await route.fulfill({ json: { found: false, node_ids: [], hops: [], paths: [] } });
    });

    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    const node = page.locator(".vibe-node").filter({ hasText: "reindex_all" }).first();
    await expect(node).toBeVisible({ timeout: 15000 });
    await node.click();

    const row = page.getByRole("button", { name: /CWE-78/ });
    await expect(row).toBeVisible({ timeout: 10000 });
    await row.click();

    await expect(page.getByText("Reachable from untrusted input: handler → sink")).toBeVisible();

    await page.getByRole("button", { name: "Show path" }).click();
    await expect.poll(() => traceUrl).toContain("from_symbol=handler");
    expect(traceUrl).toContain("to_symbol=sink");

    await page.getByRole("button", { name: /Copy AI fix prompt/ }).click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain("CWE-78");

    await page.getByRole("button", { name: "Approve exception" }).click();
    await expect.poll(() => approvePayload?.finding_id).toBe("SEC-CWE-78-abc");
    await expect(page.getByRole("button", { name: "Approve exception" })).toHaveCount(0);
  });
});
