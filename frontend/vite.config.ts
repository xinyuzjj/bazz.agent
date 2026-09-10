import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 代理到本地 Python 后端（避免 CORS），生产构建由后端 CORS 放行。
// 桌面版占用 8080 时，可用 BAZZ_DEV_PORT 指向 dev 后端实际端口（如 8081）。
const devPort = process.env.BAZZ_DEV_PORT ?? "8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${devPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
