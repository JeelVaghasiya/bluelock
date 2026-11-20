import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/local": { target: "http://localhost:9090", ws: true },
    },
  },
  build:
    mode === "onsite"
      ? { outDir: "dist-local", rollupOptions: { input: resolve(__dirname, "local.html") } }
      : { outDir: "dist", chunkSizeWarningLimit: 1500 },
}));
