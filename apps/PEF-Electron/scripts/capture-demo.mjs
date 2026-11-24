// Capture one real Electron window in the synthetic demo sequence.
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';

const [name, delayText] = process.argv.slice(2);
if (!/^[a-z_]+$/.test(name ?? '') || !/^\d+$/.test(delayText ?? '')) process.exit(2);
const root = resolve(import.meta.dirname, '..');
const out = resolve(root, '../../docs/electron02/screenshots', `${name}.png`);
mkdirSync(resolve(root, '../../docs/electron02/screenshots'), { recursive: true });
const electron = createRequire(import.meta.url)('electron');
const env = { ...process.env, PEF_MONITOR_USER_DATA: resolve(root, 'release', `capture_${name}`),
  PEF_MONITOR_SCREENSHOT: out, PEF_MONITOR_SMOKE_DELAY_MS: delayText };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, [root, '--demo', '--smoke-test'], { env, stdio: ['ignore', 'pipe', 'pipe'] });
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);
child.on('exit', (code) => { console.log(`CAPTURE ${name} ${out} exit=${code}`); process.exit(code ?? 1); });
