import { defineConfig } from "vite";

export default defineConfig({
  // No React plugin needed — we serve static HTML/CSS/JS
  root: ".",           // index.html is at frontend root
  publicDir: false,    // no separate public dir needed
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "https://rift-cicd-agent.onrender.com",
        changeOrigin: true,
        secure: true,
        // Disable buffering for SSE streams
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: "index.html",
    },
  },
});
