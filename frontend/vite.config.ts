import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the page talks to the call server on :7860 through this proxy.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:7860" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test-setup.ts"] },
});
