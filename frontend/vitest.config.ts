import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts (the dev/build config, whose /api proxy has no
// meaning for a test run) so `npm run build`'s tsc gate never has to know
// about vitest globals or test-only setup.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
