// E2E ONLY — drives the REAL PEF Monitor (built app, real Electron) through its UI with Playwright, like a
// person would: fills the station form, signs in, and reads what the screen shows. No test hook exists in
// the application. Line protocol on stdin/stdout (JSON per line) so the Python driver controls the timeline.
//
//   {"cmd":"setup","apiBaseUrl":"...","stationId":"...","visionWsUrl":"..."}
//   {"cmd":"login","email":"...","password":"..."}
//   {"cmd":"expect","appState":"monitoring","color":"green","kind":"provisional","phaseText":"...","timeout":20000,"shot":"name"}
//   {"cmd":"hide"} | {"cmd":"window"} | {"cmd":"shot","name":"x"} | {"cmd":"quit"}
import { createInterface } from 'node:readline';
import { mkdtempSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { _electron } from 'playwright-core';

const appDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const shotsDir = process.env.PEF_E2E_SHOTS || join(appDir, 'e2e', 'screenshots');
mkdirSync(shotsDir, { recursive: true });
const executablePath = createRequire(import.meta.url)('electron');
const env = { ...process.env, PEF_MONITOR_USER_DATA: process.env.PEF_MONITOR_USER_DATA || mkdtempSync(join(tmpdir(), 'pef-monitor-e2e-')) };
delete env.ELECTRON_RUN_AS_NODE;

const reply = (o) => process.stdout.write(`${JSON.stringify(o)}\n`);
const app = await _electron.launch({ executablePath, args: [appDir], env });
console.error('E2E: Electron process launched');
const page = await app.firstWindow();
page.on('console', (message) => {
  if (message.type() === 'error') console.error(`E2E renderer error: ${message.text()}`);
});
console.error('E2E: first window attached');
await page.waitForSelector('[data-testid="app-root"]', { timeout: 30000 });
console.error('E2E: renderer ready');
reply({ ok: true, event: 'launched' });

async function observe() {
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="app-root"]');
    const light = document.querySelector('[data-testid="semaphore"]');
    return {
      hasFrame: Boolean(document.querySelector('img.frame')),
      hasVideo: Boolean(document.querySelector('video.frame')),
      videoReady: (document.querySelector('video.frame')?.readyState ?? 0) >= 2,
      videoCurrentTime: document.querySelector('video.frame')?.currentTime ?? null,
      videoDuration: document.querySelector('video.frame')?.duration ?? null,
      videoWidth: document.querySelector('video.frame')?.videoWidth ?? null,
      videoHeight: document.querySelector('video.frame')?.videoHeight ?? null,
      videoErrorCode: document.querySelector('video.frame')?.error?.code ?? null,
      videoErrorMessage: document.querySelector('video.frame')?.error?.message ?? null,
      videoNetworkState: document.querySelector('video.frame')?.networkState ?? null,
      videoReadyState: document.querySelector('video.frame')?.readyState ?? null,
      appState: root?.getAttribute('data-app-state') ?? null,
      color: light?.getAttribute('data-color') ?? null,
      kind: light?.getAttribute('data-kind') ?? null,
      title: document.querySelector('[data-testid="semaphore-title"]')?.textContent ?? null,
      phaseText: document.querySelector('[data-testid="phase"]')?.textContent ?? null,
      count: document.querySelector('.count')?.textContent?.replace(/\s+/g, '') ?? null,
      waitingText: document.querySelector('.waiting-msg')?.textContent ?? null,
      source: document.querySelector('.video-badges .badge')?.textContent ?? null,
      workerStatus: [...document.querySelectorAll('.video-meta span')]
        .find((node) => node.textContent?.startsWith('Procesamiento:'))?.querySelector('strong')?.textContent ?? null,
    };
  });
}

async function windowState() {
  return app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => ({ visible: w.isVisible(), minimized: w.isMinimized() })));
}

async function shot(name) {
  if (name) await page.screenshot({ path: join(shotsDir, `${name}.png`) });
}

const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue;
  const c = JSON.parse(line);
  try {
    if (c.cmd === 'setup') {
      await page.waitForSelector('[data-testid="setup"]', { timeout: 20000 });
      await page.getByLabel('URL del API Gateway (o de los servicios)').fill(c.apiBaseUrl);
      await page.getByLabel('Identificador de la estación de conteo (UUID)').fill(c.stationId);
      if (c.visionWsUrl) {
        await page.locator('summary').click();
        await page.getByLabel('WebSocket de VisionWorker').fill(c.visionWsUrl);
      }
      await shot(c.shot);
      await page.getByRole('button', { name: 'Guardar configuración' }).click();
      await page.getByLabel('Correo del Operador').waitFor({ timeout: 15000 });
      reply({ ok: true });
    } else if (c.cmd === 'login') {
      await page.getByLabel('Correo del Operador').fill(c.email);
      await page.getByLabel('Contraseña').fill(c.password);
      await page.getByRole('button', { name: 'Iniciar sesión' }).click();
      const end = Date.now() + 20000;
      let o = await observe();
      while (Date.now() < end && o.appState === 'login_required') {
        await page.waitForTimeout(250);
        o = await observe();
      }
      const error = await page.locator('[role="alert"]').textContent().catch(() => null);
      reply({ ok: o.appState !== 'login_required' && o.appState !== 'setup', observed: o, error });
    } else if (c.cmd === 'expect') {
      const end = Date.now() + (c.timeout ?? 20000);
      let o = await observe();
      const match = (x) =>
        (!c.appState || x.appState === c.appState) && (!c.color || x.color === c.color) && (!c.kind || x.kind === c.kind) &&
        (c.hasVideo === undefined || x.hasVideo === c.hasVideo) &&
        (c.videoReady === undefined || x.videoReady === c.videoReady) &&
        (!c.workerStatus || x.workerStatus === c.workerStatus) &&
        (!c.phaseText || (x.phaseText ?? '').includes(c.phaseText)) && (!c.titleIncludes || (x.title ?? '').includes(c.titleIncludes));
      while (Date.now() < end && !match(o)) {
        await page.waitForTimeout(200);
        o = await observe();
      }
      await shot(c.shot);
      reply({ ok: match(o), observed: o, windows: await windowState() });
    } else if (c.cmd === 'seek') {
      await page.locator('video.frame').evaluate((video, seconds) => {
        video.pause();
        video.currentTime = seconds;
      }, c.time);
      await page.waitForFunction((seconds) => {
        const video = document.querySelector('video.frame');
        return video && video.readyState >= 2 && Math.abs(video.currentTime - seconds) < 0.35;
      }, c.time, { timeout: 15000 });
      await shot(c.shot);
      reply({ ok: true, observed: await observe() });
    } else if (c.cmd === 'hide') {
      await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach((w) => w.hide()));
      reply({ ok: true, windows: await windowState() });
    } else if (c.cmd === 'window') {
      reply({ ok: true, windows: await windowState() });
    } else if (c.cmd === 'shot') {
      await shot(c.name);
      reply({ ok: true });
    } else if (c.cmd === 'quit') {
      await app.evaluate(({ app: a }) => a.exit(0)).catch(() => undefined);
      reply({ ok: true });
      break;
    } else {
      reply({ ok: false, error: `unknown ${c.cmd}` });
    }
  } catch (err) {
    reply({ ok: false, error: String(err), observed: await observe().catch(() => null) });
  }
}
process.exit(0);
