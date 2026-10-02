import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the UI with hot reload and forwards /api to a running backend.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: process.env.STUDIO_API ?? "http://127.0.0.1:8080" } },
  },
  build: { outDir: "dist", target: "es2022", sourcemap: false },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
