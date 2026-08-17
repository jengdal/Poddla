import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    cssTarget: ['chrome123', 'firefox120', 'safari17.5'],
    outDir: 'src/youtube_to_podcast/static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'static-src/opui.js',
      output: {
        entryFileNames: '[name].js',
        assetFileNames: '[name][extname]',
      },
    },
  },
})
