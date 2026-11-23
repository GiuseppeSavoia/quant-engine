import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
// (i test di vitest usano i valori predefiniti: ambiente node)

// Sviluppo: `npm run dev` (porta 5173) inoltra /api all'API Python (python -m api --demo, porta 8600).
// Produzione: `npm run build` scrive web/dist, che l'API serve da sola (una porta, nessun CORS).
// I loghi stanno in ../assets (stessi file della webapp Streamlit), pubblicati alla radice.
export default defineConfig({
  plugins: [react()],
  publicDir: "../assets",
  server: { port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:8600" } },
  build: { outDir: "dist", emptyOutDir: true },

});
