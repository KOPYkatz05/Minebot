import { defineConfig } from 'vite';
export default defineConfig({
  build: { rollupOptions: { output: { manualChunks: { graphics: ['three', 'three/addons/controls/OrbitControls.js'], react: ['react', 'react-dom'] } } } },
});
