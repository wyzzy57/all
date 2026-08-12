import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

const apiProxy = {
  target: "http://[::1]:8000",
  changeOrigin: true
};

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/account": apiProxy,
      "/admin": apiProxy,
      "/auth": apiProxy,
      "/base-models": apiProxy,
      "/datasets": apiProxy,
      "/frameworks": apiProxy,
      "/health": apiProxy,
      "/label-projects": apiProxy,
      "/log-streams": apiProxy,
      "/llm": apiProxy,
      "/nodes": apiProxy,
      "/pipelines": apiProxy,
      "/resource-pools": apiProxy,
      "/services": apiProxy,
      "/statistics": apiProxy,
      "/tasks": apiProxy,
      "/trained-models": apiProxy,
      "/training-jobs": apiProxy,
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true
      }
    }
  }
});
