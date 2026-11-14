export default {
  build: {
    lib: { entry: 'src/extension.mjs', formats: ['es'] },
    rollupOptions: { output: { entryFileNames: 'extension.js', inlineDynamicImports: true } },
  },
};
