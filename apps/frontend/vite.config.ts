import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

const apiProxy = {
  target: "http://127.0.0.1:8000",
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
      "/base-models": apiProxy,
      "/datasets": apiProxy,
      "/health": apiProxy,
      "/label-projects": apiProxy,
      "/nodes": apiProxy,
      "/pipelines": apiProxy,
      "/resource-pools": apiProxy,
      "/services": apiProxy,
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
