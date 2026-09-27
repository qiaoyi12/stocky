/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite build output defaults to `dist`, which the FastAPI backend mounts as the
// SPA under `/`. During local dev, proxy `/api` to the backend on port 8000 so
// the frontend can call the API without CORS configuration.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
