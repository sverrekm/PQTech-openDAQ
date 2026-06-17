import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Relative asset-stiar (./assets/...) so SPA-en lastar likt frå rota OG
  // bak node-proxyen (/node-proxy/<id>/) utan å vere avhengig av 307-redirect-
  // omskriving av /assets/ — det gav cacha redirectar og dobbel-prefiks.
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
