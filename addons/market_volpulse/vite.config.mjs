import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    lib: { entry: 'src/extension.mjs', formats: ['es'] },
    rollupOptions: { output: { entryFileNames: 'extension.js', inlineDynamicImports: true } },
    minify: 'esbuild',
  },
});
