import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: "http://localhost:5173",
    headless: true,
    executablePath: "I:\\01-Master_Code\\Tools\\chrome-Test\\chrome.exe",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run python -m vibetrace.server_api",
      cwd: "../..",
      port: 8000,
      reuseExistingServer: true,
      timeout: 15000,
    },
    {
      command: "bun run dev --port 5173",
      port: 5173,
      reuseExistingServer: true,
      timeout: 15000,
    },
  ],
});
