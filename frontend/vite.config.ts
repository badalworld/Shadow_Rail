import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The dashboard is built straight into the backend so FastAPI serves
// API + UI from one origin (no CORS/proxy issues in production).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../backend/web',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    // Split the big vendors into their own cached chunks: three.js (~1 MB)
    // and React change far less often than the app code, and parallel
    // downloads overlap, so the first paint lands sooner.
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/three/') || id.includes('/@react-three/')) return 'three'
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) return 'react'
          if (id.includes('/framer-motion/') || id.includes('/motion-dom/') || id.includes('/motion-utils/')) return 'motion'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: false,
    allowedHosts: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8080', ws: true },
    },
  },
})
