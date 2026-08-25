import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";


export default defineConfig({
  optimizeDeps: {
    exclude: ["@vitest/browser-playwright", "playwright", "playwright-core"],
    include: ["lucide-react", "pdfjs-dist"],
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    browser: {
      enabled: true,
      headless: true,
      api: {
        host: "127.0.0.1",
        port: 4174,
        strictPort: true,
      },
      instances: [{ browser: "chromium" }],
      provider: playwright(),
    },
  },
});
