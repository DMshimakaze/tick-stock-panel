import { defineConfig } from 'vite';
export default defineConfig({ build: {
  outDir: 'dist', minify: 'esbuild',
  lib: { entry: 'src/extension.mjs', formats: ['es'] },
  rollupOptions: { output: { entryFileNames: 'extension.js', inlineDynamicImports: true } },
} });
