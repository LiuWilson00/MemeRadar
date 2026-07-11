import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 後端 API（python -m memeradar.api）
const API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/recommend": API,
      "/parse-screenshot": API,
      "/feedback": API,
      "/memes": API,
      "/meta": API,
      "/health": API,
      "/history": API,
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
