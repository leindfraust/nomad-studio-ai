import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Development proxies the API so the browser makes same-origin requests.
// Production is served by FastAPI from `dist`, also same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.tsx"],
    restoreMocks: true,
  },
});
