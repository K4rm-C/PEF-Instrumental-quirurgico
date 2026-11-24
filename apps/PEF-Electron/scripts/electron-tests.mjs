// Tests on the REAL Electron binary (npm run test:electron, after npm run build). Demo mode only: no backend,
// no registry changes (autostart is never toggled here), isolated profiles in the OS temp folder.
//  1. Security smoke: app protocol, context isolation, sandbox, no Node in the renderer, minimal bridge,
//     navigation and window.open blocked, tray created, demo session detected.
//  2. Single instance: a second launch exits and the first one logs the focus request.
//  3. Protocol activation: pef-monitor://open on a second launch focuses the existing window.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const electron = createRequire(import.meta.url)('electron'); // path to the binary
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE; // set by VS Code terminals; it would start Electron as plain Node

let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  ${detail}` : ''}`);
  if (!ok) failures += 1;
};

function run(args, extraEnv, { timeoutMs = 60000 } = {}) {
  return new Promise((resolve) => {
    const child = spawn(electron, [root, ...args], { env: { ...env, ...extraEnv }, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.on('exit', (code) => {
      clearTimeout(timer);
      resolve({ code, out });
    });
    resolve.child = child;
  });
}

// 1. Security smoke
{
  const profile = mkdtempSync(join(tmpdir(), 'pef-monitor-e1-'));
  const { code, out } = await run(['--demo', '--smoke-test'], { PEF_MONITOR_USER_DATA: profile, PEF_MONITOR_SMOKE_DELAY_MS: '9000' });
  const line = out.split('\n').find((l) => l.startsWith('SMOKE '));
  const r = line ? JSON.parse(line.slice(6)) : {};
  check('smoke exits cleanly', code === 0, `code=${code}`);
  check('renderer loaded from pef-app://monitor', r.loadedFromAppProtocol === true);
  check('no require/process in the renderer', r.rendererIsolation?.require === 'undefined' && r.rendererIsolation?.process === 'undefined');
  check('bridge exposes only the documented functions', JSON.stringify(r.rendererIsolation?.keys) ===
    JSON.stringify(['getConfig', 'getSnapshot', 'hideToTray', 'login', 'logout', 'onSnapshot', 'reportLocalCameras', 'retry', 'saveConfig', 'setAutostart']));
  check('contextIsolation + sandbox, no nodeIntegration', r.webPreferences?.contextIsolation === true && r.webPreferences?.sandbox === true && r.webPreferences?.nodeIntegration === false);
  check('external navigation blocked', r.navigationBlocked === true);
  check('window.open blocked', r.windowOpenBlocked === true);
  check('tray icon created', r.tray === true);
  check('demo session detected and window activated', r.appState === 'monitoring' && r.domAppState === 'monitoring', `state=${r.appState}`);
}

// 2 + 3. Single instance and protocol activation
{
  const profile = mkdtempSync(join(tmpdir(), 'pef-monitor-e2-'));
  const envA = { PEF_MONITOR_USER_DATA: profile, PEF_MONITOR_SMOKE_DELAY_MS: '14000' };
  const first = run(['--demo', '--smoke-test'], envA, { timeoutMs: 60000 });
  await new Promise((r) => setTimeout(r, 5000));
  const t0 = Date.now();
  const second = await run(['--demo'], { PEF_MONITOR_USER_DATA: profile }, { timeoutMs: 15000 });
  check('second instance exits immediately', second.code === 0 && Date.now() - t0 < 10000, `code=${second.code} ${Date.now() - t0}ms`);
  const third = await run(['--demo', 'pef-monitor://open'], { PEF_MONITOR_USER_DATA: profile }, { timeoutMs: 15000 });
  check('protocol launch exits and hands over', third.code === 0, `code=${third.code}`);
  const a = await first;
  check('first instance kept running until its own end', a.code === 0, `code=${a.code}`);
  const log = readFileSync(join(profile, 'demo-profile', 'logs', 'monitor.log'), 'utf8');
  check('first instance focused on second launch', log.includes('second instance launch: focusing existing window'));
  check('first instance handled pef-monitor://open', log.includes('protocol activation: focusing existing window'));
  check('log contains no secrets', !/eyJ[A-Za-z0-9_-]{10,}\.|password/i.test(log));
}

console.log(failures ? `\n${failures} check(s) failed` : '\nall Electron checks passed');
process.exit(failures ? 1 : 0);
