import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import pkg from "../package.json";

// Separate entry, no Python proxy. This bundle must never ship as the production app.
const root = fileURLToPath(new URL(".", import.meta.url));
export default defineConfig({
  root,
  base: "./",
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: { host: "127.0.0.1", port: 5186, strictPort: true },
  build: {
    outDir: fileURLToPath(new URL("../outputs/ui-preview", import.meta.url)),
    emptyOutDir: false,
    rollupOptions: { input: fileURLToPath(new URL("./preview.html", import.meta.url)) },
  },
});
