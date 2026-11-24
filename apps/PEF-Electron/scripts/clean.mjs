// Removes build output (dist/) only. Never touches release/ installers, user data or anything outside this app.
import { rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
rmSync(join(root, 'dist'), { recursive: true, force: true });
