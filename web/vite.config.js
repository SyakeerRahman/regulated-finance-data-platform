import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The build lands where FastAPI serves static files, so one server answers in production.
  build: { outDir: "../api/static", emptyOutDir: true },
  // In development Vite serves the app and forwards the API to uvicorn.
  server: { proxy: { "/api": "http://localhost:8097" } },
});
