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
      // `npm run sim:dev` talks to the simulator (:8090); everything else to the SaaS api (:8000)
      "/api": mode === "sim" ? "http://localhost:8090" : "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/local": { target: "http://localhost:9090", ws: true },
    },
  },
  build:
    mode === "onsite"
      ? { outDir: "dist-local", rollupOptions: { input: resolve(__dirname, "local.html") } }
      : mode === "sim"
        ? { outDir: "dist-sim", rollupOptions: { input: resolve(__dirname, "sim.html") } }
        : { outDir: "dist", chunkSizeWarningLimit: 1500 },
}));
