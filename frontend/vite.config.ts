import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In dev, /api is proxied to the FastAPI backend, so no CORS setup is needed.
    proxy: { "/api": process.env.VITE_PROXY_TARGET ?? "http://localhost:8000" },
  },
});
