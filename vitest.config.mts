import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // Mirror tsconfig's `@/*` path alias.
    alias: { '@': fileURLToPath(new URL('.', import.meta.url)) },
  },
  // tsconfig leaves JSX for Next to compile; vitest has to compile it itself.
  oxc: { jsx: { runtime: 'automatic' } },
  test: {
    // Python tests share this directory (test_*.py); only *.test.ts(x) is ours.
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
    environment: 'node',
  },
});
