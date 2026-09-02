import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next, but matched at any depth. Anchored at
    // the root they miss a build directory that lands anywhere else — a dev server
    // started with the project directory applied twice writes one to web/web/.next,
    // and linting 47 files of Turbopack output reports 213 errors in code nobody
    // wrote.
    "**/.next/**",
    "**/out/**",
    "**/build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
