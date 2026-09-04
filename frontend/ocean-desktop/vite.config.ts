import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const desktopRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: './',
  root: 'src/renderer',
  plugins: [react()],
  resolve: {
    alias: {
      // Packages under ../packages are imported as source; bare specifiers
      // inside them must resolve against this app's node_modules.
      'react': resolve(desktopRoot, 'node_modules/react'),
      'lucide-react': resolve(desktopRoot, 'node_modules/lucide-react'),
    },
  },
  build: {
    outDir: '../../dist/renderer',
    emptyOutDir: true,
    rolldownOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/maplibre-gl/')) return 'maplibre';
          if (id.includes('/node_modules/uplot/')) return 'uplot';
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react';
          return undefined;
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5177,
    fs: {allow: [desktopRoot, resolve(desktopRoot, '..', 'packages')]},
  },
});
