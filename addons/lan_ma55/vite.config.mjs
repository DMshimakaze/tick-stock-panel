import { defineConfig } from 'vite';

// Page Pack template's self-contained ES-module contract, without a React runtime.
export default defineConfig({
  build: {
    outDir: 'dist',
    minify: 'esbuild',
    lib: { entry: 'src/extension.mjs', formats: ['es'] },
    rollupOptions: { output: { entryFileNames: 'extension.js', inlineDynamicImports: true } },
  },
});
