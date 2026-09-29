import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PUBLIC_PATHS, SPA_ENTRY_PATHS } from '../src/publicRoutes.js';

const here = dirname(fileURLToPath(import.meta.url));
const dist = join(here, '..', 'dist');
const source = join(dist, 'index.html');

// Fail the build if Vite did not produce the entrypoint. A silent success here
// would deploy the exact direct-load 404 this step exists to prevent.
await readFile(source, 'utf8');

const routes = [...PUBLIC_PATHS, ...SPA_ENTRY_PATHS];
for (const route of routes) {
  const targetDir = join(dist, route.replace(/^\//, ''));
  await mkdir(targetDir, { recursive: true });
  await copyFile(source, join(targetDir, 'index.html'));
}

console.log(`Generated ${routes.length} static SPA entrypoints (${SPA_ENTRY_PATHS.length} client routes).`);
