import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  {ignores: ['dist/', 'dist-electron/', 'sidecar/', 'tests/', 'node_modules/']},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: {'react-hooks': reactHooks},
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      '@typescript-eslint/no-unused-vars': ['warn', {argsIgnorePattern: '^_'}],
    },
  },
  {
    // contained-path rejects control characters in path segments on purpose
    // (security check); the control-char range in the regex is the point.
    files: ['src/shared/contained-path.ts'],
    rules: {'no-control-regex': 'off'},
  },
);
