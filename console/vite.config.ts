import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 後端 API（python -m memeradar.api）
const API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 單一 SPA 入口（index.html）；前台 "/" 與後台 "/admin/*" 由 client-side router 分流
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
      "/tasks": API, // 非同步推薦任務（前台送出/輪詢/歷史）
      "/settings": API, // 後台各任務模型設定
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
