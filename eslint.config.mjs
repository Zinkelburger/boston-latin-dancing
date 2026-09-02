import { dirname } from 'path';
import { fileURLToPath } from 'url';
import { FlatCompat } from '@eslint/eslintrc';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// eslint-config-next 15.x still ships its rules as legacy shareable configs;
// FlatCompat bridges them into the flat format ESLint 9 expects.
const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  {
    ignores: [
      '.next/**',
      'out/**',
      'node_modules/**',
      'next-env.d.ts',
      // Python tooling and generated data live outside the web build.
      'scripts/**',
      'backend/**',
      'mcp-server/**',
      'automation/**',
      'data/**',
    ],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
];

export default eslintConfig;
