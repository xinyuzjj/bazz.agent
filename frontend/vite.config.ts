import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "../package.json";

// 开发时把 /api 代理到本地 Python 后端（避免 CORS），生产构建由后端 CORS 放行。
// 桌面版占用 8080 时，可用 BAZZ_DEV_PORT 指向 dev 后端实际端口（如 8081）。
const devPort = process.env.BAZZ_DEV_PORT ?? "8080";

export default defineConfig({
  plugins: [react()],
  // 全局注入 package.json 真实版本号（Shell 顶栏展示，杜绝硬编码漂移）
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${devPort}`,
        changeOrigin: true,
        ws: true, // WebSocket（/api/ws 行情流）dev 代理也需升级连接
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
