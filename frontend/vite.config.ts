import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // 8000、5173 被别的项目占用，所以改用 5180；strictPort：被占用就直接报错，不要自己跳到别的端口（不然后端的 CORS 对不上）
  server: { port: 5180, strictPort: true },
});
