import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 後端 API（python -m memeradar.api）
const API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // 雙入口：/ = 手機 client（前台，公開），/admin.html = 調適 Console（後台）
    rollupOptions: {
      input: {
        main: "index.html",
        admin: "admin.html",
      },
    },
  },
  server: {
    // 放行 ngrok 隧道網域（Vite 6 預設擋未知 Host → "Blocked request"）
    allowedHosts: [".ngrok-free.app", ".ngrok.app", ".ngrok-free.dev", ".ngrok.dev", ".ngrok.io"],
    proxy: {
      "/recommend": API,
      "/parse-screenshot": API,
      "/feedback": API,
      "/memes": API,
      "/meta": API,
      "/health": API,
      "/history": API,
      "/review": API,
      "/report": API,
      "/vlm": API,
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
