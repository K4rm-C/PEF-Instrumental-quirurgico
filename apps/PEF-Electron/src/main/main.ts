// PEF Monitor — Electron main process (ELECTRON-01).
// A visual monitor of the session the Operator runs from the portal. It never creates, starts, validates,
// approves or closes sessions and never opens the camera.
import * as path from 'node:path';
import * as tls from 'node:tls';
import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  nativeImage,
  safeStorage,
  Tray,
  type IpcMainInvokeEvent,
} from 'electron';
import { autostartSettings, isOpenRequest } from './autostart';
import { ApiError, MemorySecretStore, PefApi, type SecretStore } from './api';
import { ConfigError, ConfigStore, isConfigured, normalizeConfig, resolvedUrls, type StationConfig } from './config';
import { EncryptedFileSecretStore } from './credentials';
import { DEMO_STATION_ID, DemoApi, DemoWorld, demoWsFactory } from './demo';
import { Logger } from './logger';
import { StationMonitor, type MonitorApi } from './monitor';
import { APP_ENTRY, hardenSession, hardenWebContents, isAppUrl, registerAppScheme, serveRenderer } from './security';
import type { WsFactory, WsLike } from './visionObserver';
import {
  IPC,
  IpcValidationError,
  isTrustedSenderUrl,
  validateBoolean,
  validateLocalCameras,
  validateLogin,
  validateNoArgs,
  validateSaveConfig,
  type IpcResult,
} from '../shared/ipc';
import type { MonitorSnapshot, PublicConfig } from '../shared/types';
import { phaseText, translate } from '../shared/i18n';

const argv = process.argv.slice(1);
const DEV = argv.includes('--dev');
const DEMO = argv.includes('--demo');
const SMOKE = argv.includes('--smoke-test');
const START_HIDDEN_ARG = argv.includes('--hidden');
const PROTOCOL = 'pef-monitor';
const ALLOW_INSECURE_LAN = process.env.PEF_MONITOR_ALLOW_INSECURE_LAN === '1';

// Isolated profile for tests / several stations on one PC (never shared with the default profile).
if (process.env.PEF_MONITOR_USER_DATA) app.setPath('userData', path.resolve(process.env.PEF_MONITOR_USER_DATA));
if (DEMO) app.setPath('userData', path.join(app.getPath('userData'), 'demo-profile'));

// One process per Windows user: a second launch only brings the existing window forward.
if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}

app.enableSandbox();
registerAppScheme();

const log = new Logger(path.join(app.getPath('userData'), 'logs'), DEV || SMOKE);

// Trust the Windows certificate store too (hospital internal CAs), never less than Node's bundled roots.
try {
  const t = tls as unknown as { getCACertificates?: (k: string) => string[]; setDefaultCACertificates?: (c: string[]) => void };
  if (t.getCACertificates && t.setDefaultCACertificates) {
    t.setDefaultCACertificates([...t.getCACertificates('default'), ...t.getCACertificates('system')]);
  }
} catch (err) {
  log.warn('system CA store not loaded', err);
}

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let monitor: StationMonitor;
let api: PefApi | null = null;
let monitorApi: MonitorApi;
let configStore: ConfigStore;
let config: StationConfig;
let secrets: SecretStore;
let lastSnapshot: MonitorSnapshot | null = null;
let lastFrameVisible = true;

function assetPath(...parts: string[]): string {
  return path.join(app.getAppPath(), 'build', ...parts);
}

function publicConfig(): PublicConfig {
  return {
    configured: DEMO || isConfigured(config),
    apiBaseUrl: config.apiBaseUrl,
    authBaseUrl: config.authBaseUrl,
    sessionBaseUrl: config.sessionBaseUrl,
    visionWsUrl: config.visionWsUrl,
    stationId: DEMO ? DEMO_STATION_ID : config.stationId,
    autostart: app.getLoginItemSettings().openAtLogin,
    startHidden: config.startHidden,
    hideWhenIdle: config.hideWhenIdle,
    credentialsStored: DEMO || Boolean(secrets?.get()),
    secureStorage: secrets?.persistent ?? false,
  };
}

// --- window / tray ------------------------------------------------------------------------------------------

function createWindow(showOnReady: boolean): void {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 620,
    show: false,
    title: 'PEF Monitor',
    backgroundColor: '#0d141c',
    autoHideMenuBar: true,
    icon: assetPath('icon.png'),
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      spellcheck: false,
      navigateOnDragDrop: false,
      devTools: DEV,
    },
  });
  win.once('ready-to-show', () => {
    if (showOnReady) win?.show();
  });
  win.on('close', (event) => {
    if (!quitting) {
      event.preventDefault(); // closing the window keeps the monitor alive in the tray
      win?.hide();
    }
  });
  win.on('show', () => pushSnapshot(lastSnapshot));
  win.on('restore', () => pushSnapshot(lastSnapshot));
  void win.loadURL(APP_ENTRY);
}

function showWindow(): void {
  if (!win || win.isDestroyed()) createWindow(true);
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  win.flashFrame(true);
  setTimeout(() => win?.flashFrame(false), 4000);
}

function buildTrayMenu(): Menu {
  return Menu.buildFromTemplate([
    { label: 'Mostrar monitor', click: () => showWindow() },
    {
      label: 'Iniciar con Windows',
      type: 'checkbox',
      checked: app.getLoginItemSettings().openAtLogin,
      enabled: !DEMO,
      click: (item) => setAutostart(item.checked),
    },
    { type: 'separator' },
    {
      label: 'Salir',
      click: () => {
        quitting = true;
        app.quit();
      },
    },
  ]);
}

function createTray(): void {
  const image = nativeImage.createFromPath(assetPath('tray.png'));
  tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
  tray.setToolTip(DEMO ? 'PEF Monitor (demostración)' : 'PEF Monitor');
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', () => showWindow());
}

function setAutostart(enabled: boolean): void {
  if (DEMO) return;
  // Windows: HKCU\...\Run entry for this user. Starts hidden in the tray; the window opens when a session starts.
  app.setLoginItemSettings(autostartSettings(enabled, app.isPackaged, process.execPath, app.getAppPath()));
  config = { ...config, autostart: enabled };
  configStore.save(config);
  tray?.setContextMenu(buildTrayMenu());
  log.info(`autostart ${enabled ? 'enabled' : 'disabled'}`);
}

// --- snapshots ----------------------------------------------------------------------------------------------

function pushSnapshot(snapshot: MonitorSnapshot | null): void {
  if (!snapshot || !win || win.isDestroyed()) return;
  // While hidden in the tray or minimised, frames are not shipped to the renderer (memory/CPU); the next
  // snapshot after 'show'/'restore' carries the current frame again.
  const visible = win.isVisible() && !win.isMinimized();
  if (visible !== lastFrameVisible) {
    lastFrameVisible = visible;
    log.info(visible ? 'window visible: frames resumed' : 'window hidden/minimised: frames paused');
  }
  const payload = visible ? snapshot : { ...snapshot, frame: null };
  win.webContents.send(IPC.snapshotEvent, payload);
  if (tray) {
    const s = snapshot.semaphore;
    tray.setToolTip(`PEF Monitor — ${phaseText(snapshot.locale, snapshot.phase, snapshot.session?.statusCode)}${snapshot.session ? ` — ${translate(snapshot.locale, s.title)}` : ''}`);
  }
}

async function serveRecording(runId: string, request: Request): Promise<Response> {
  const snap = lastSnapshot;
  if (!api || !snap?.session || snap.appState !== 'monitoring' ||
      snap.vision.source !== 'recorded' || snap.recording?.runId !== runId ||
      snap.connection.backend !== 'ok') return new Response('Unavailable', { status: 404 });
  const sessionId = snap.session.id;
  try {
    // Every range request rechecks JWT, institution, station, Operator assignment and active status.
    const current = await api.sessionMonitor(config.stationId, sessionId);
    if (current.session_id !== sessionId || current.status_code !== 'in_progress' ||
        current.station_id !== config.stationId) return new Response('Unavailable', { status: 404 });
    const ticket = await api.liveTicket(config.stationId, sessionId);
    const ws = new URL(resolvedUrls(config, ticket.ws_url).ws);
    ws.protocol = ws.protocol === 'wss:' ? 'https:' : 'http:';
    ws.pathname = `/sessions/${encodeURIComponent(sessionId)}/recordings/${runId}`;
    ws.search = '';
    const range = request.headers.get('range');
    const upstream = await fetch(ws, {
      headers: { Authorization: `Bearer ${ticket.ticket}`, ...(range ? { Range: range } : {}) },
      redirect: 'error',
      signal: AbortSignal.timeout(15000),
    });
    const headers = new Headers({ 'Content-Type': 'video/mp4', 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff' });
    for (const name of ['content-length', 'content-range', 'accept-ranges']) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    log.warn('recording access unavailable', err);
    return new Response('Unavailable', { status: 503 });
  }
}

// --- IPC ----------------------------------------------------------------------------------------------------

function trusted(event: IpcMainInvokeEvent): boolean {
  return Boolean(win && event.sender === win.webContents && isTrustedSenderUrl(event.senderFrame?.url));
}

function handle<T>(channel: string, fn: (payload: unknown) => Promise<T> | T): void {
  ipcMain.handle(channel, async (event, payload): Promise<IpcResult<T>> => {
    if (!trusted(event)) {
      log.warn(`IPC rejected from untrusted sender on ${channel}`);
      return { ok: false, error: 'Origen no autorizado' };
    }
    try {
      return { ok: true, value: await fn(payload) };
    } catch (err) {
      if (err instanceof IpcValidationError || err instanceof ConfigError) return { ok: false, error: err.message };
      if (err instanceof ApiError) return { ok: false, error: loginErrorMessage(err) };
      log.error(`IPC ${channel} failed`, err);
      return { ok: false, error: 'Error interno' };
    }
  });
}

function loginErrorMessage(err: ApiError): string {
  if (err.code === 'not_station_operator') return err.message;
  if (err.status === 0) return 'No se pudo conectar con el servidor de autenticación.';
  if (err.status === 401) return 'Correo o contraseña incorrectos, o cuenta inactiva.';
  if (err.status === 423 || err.status === 429) return 'Demasiados intentos. Espere antes de reintentar.';
  return `Error del servidor (${err.status}).`;
}

function registerIpc(): void {
  handle(IPC.getSnapshot, (p) => {
    validateNoArgs(p);
    return lastSnapshot;
  });
  handle(IPC.getConfig, (p) => {
    validateNoArgs(p);
    return publicConfig();
  });
  handle(IPC.saveConfig, (p) => {
    if (DEMO) throw new IpcValidationError('No disponible en modo demostración');
    const input = validateSaveConfig(p);
    const next = normalizeConfig(input, config, ALLOW_INSECURE_LAN);
    const stationChanged = next.stationId !== config.stationId;
    config = next;
    configStore.save(config);
    const urls = resolvedUrls(config);
    api?.setUrls({ auth: urls.auth, session: urls.session });
    log.info(`configuration saved (station ${config.stationId}${stationChanged ? ', changed' : ''})`);
    monitor.refreshNow();
    return publicConfig();
  });
  handle(IPC.login, async (p) => {
    if (DEMO || !api) throw new IpcValidationError('No disponible en modo demostración');
    if (!isConfigured(config)) throw new IpcValidationError('Configure primero la estación');
    const { email, password } = validateLogin(p);
    const identity = await api.login(email, password);
    log.info('station operator signed in');
    monitor.refreshNow();
    return { email: identity.email };
  });
  handle(IPC.logout, async (p) => {
    validateNoArgs(p);
    if (DEMO || !api) return null;
    await api.logout();
    monitor.signedOut('Sesión de la estación cerrada.');
    log.info('station operator signed out');
    return null;
  });
  handle(IPC.setAutostart, (p) => {
    setAutostart(validateBoolean(p, 'autostart'));
    return publicConfig();
  });
  handle(IPC.hideToTray, (p) => {
    validateNoArgs(p);
    win?.hide();
    return null;
  });
  handle(IPC.reportLocalCameras, (p) => {
    monitor.setLocalVideoInputs(validateLocalCameras(p));
    return null;
  });
  handle(IPC.retry, (p) => {
    validateNoArgs(p);
    monitor.refreshNow();
    return null;
  });
}

// --- protocol activation (pef-monitor://open) ---------------------------------------------------------------

app.on('second-instance', (_event, commandLine) => {
  log.info(isOpenRequest(commandLine) ? 'protocol activation: focusing existing window' : 'second instance launch: focusing existing window');
  if (!win) return; // still booting: the window will be shown by the normal start-up
  showWindow();
});

// --- boot ---------------------------------------------------------------------------------------------------

function nodeWsFactory(): WsFactory {
  const Impl = (globalThis as unknown as { WebSocket?: new (url: string) => WsLike }).WebSocket;
  if (!Impl) throw new Error('WebSocket not available in this runtime');
  return (url) => new Impl(url);
}

app.whenReady().then(() => {
  log.info(`PEF Monitor ${app.getVersion()} starting${DEMO ? ' (demo)' : ''}${DEV ? ' (dev)' : ''}`);
  if (ALLOW_INSECURE_LAN) log.warn('PEF_MONITOR_ALLOW_INSECURE_LAN=1: plain http/ws allowed on the LAN (lab only)');
  Menu.setApplicationMenu(null);
  hardenSession((m) => log.warn(m));
  hardenWebContents((m) => log.warn(m));
  serveRenderer(path.join(__dirname, '..', 'renderer'), serveRecording);

  configStore = new ConfigStore(app.getPath('userData'));
  config = configStore.load();
  secrets = DEMO
    ? new MemorySecretStore()
    : new EncryptedFileSecretStore(path.join(app.getPath('userData')), safeStorage, (m) => log.warn(m));

  let wsFactory: WsFactory;
  if (DEMO) {
    const world = new DemoWorld();
    monitorApi = new DemoApi(world);
    wsFactory = demoWsFactory(world);
  } else {
    const urls = resolvedUrls(config);
    api = new PefApi({ auth: urls.auth, session: urls.session }, secrets);
    monitorApi = api;
    wsFactory = nodeWsFactory();
  }

  monitor = new StationMonitor({
    api: monitorApi,
    stationId: () => (DEMO ? DEMO_STATION_ID : config.stationId),
    configured: () => DEMO || isConfigured(config),
    resolveWsUrl: (hint) => resolvedUrls(config, hint).ws,
    wsFactory,
    emit: (snapshot) => {
      lastSnapshot = snapshot;
      pushSnapshot(snapshot);
    },
    activate: () => {
      log.info('active session: showing monitor');
      showWindow();
    },
    idle: () => {
      if (config.hideWhenIdle && win && !win.isDestroyed()) win.hide();
    },
    log,
    autostart: () => app.getLoginItemSettings().openAtLogin,
    demo: DEMO,
    options: DEMO ? { reviewHoldMs: 10000, endedHoldMs: 10000 } : undefined,
  });

  registerIpc();
  if (app.isPackaged && !DEMO) app.setAsDefaultProtocolClient(PROTOCOL);
  createTray();
  const startHidden = (START_HIDDEN_ARG || config.startHidden) && !SMOKE;
  createWindow(!startHidden);
  monitor.start();
  if (isOpenRequest(argv)) showWindow();

  if (SMOKE) runSmokeTest();
});

app.on('window-all-closed', () => {
  // Stay in the tray (Windows). Quitting is explicit from the tray menu.
});

app.on('before-quit', () => {
  quitting = true;
  monitor?.stop();
  tray?.destroy();
  log.info('PEF Monitor stopped');
});

// --- smoke test (npm run smoke) -------------------------------------------------------------------------------

function runSmokeTest(): void {
  const result: Record<string, unknown> = {};
  const done = (code: number) => {
    process.stdout.write(`SMOKE ${JSON.stringify(result)}\n`);
    quitting = true;
    app.exit(code);
  };
  const timeout = setTimeout(() => {
    result.error = 'timeout';
    done(1);
  }, (Number(process.env.PEF_MONITOR_SMOKE_DELAY_MS) || 9000) + 20000);
  win?.webContents.once('did-finish-load', async () => {
    try {
      result.loadedFromAppProtocol = isAppUrl(win!.webContents.getURL());
      result.rendererIsolation = await win!.webContents.executeJavaScript(
        `({ require: typeof window.require, process: typeof window.process, bridge: typeof window.pef, keys: window.pef ? Object.keys(window.pef).sort() : [] })`,
      );
      const wc = win!.webContents as unknown as { getLastWebPreferences?: () => Record<string, unknown> | null };
      const prefs = wc.getLastWebPreferences?.() ?? null;
      result.webPreferences = prefs
        ? { contextIsolation: prefs.contextIsolation, nodeIntegration: prefs.nodeIntegration, sandbox: prefs.sandbox }
        : null;
      result.navigationBlocked = await win!.webContents
        .executeJavaScript(`(() => { try { window.location.href = 'https://example.com'; } catch (e) {} return true; })()`)
        .then(() => new Promise((r) => setTimeout(() => r(isAppUrl(win!.webContents.getURL())), 800)));
      result.windowOpenBlocked = await win!.webContents.executeJavaScript(`window.open('https://example.com') === null`);
      result.tray = Boolean(tray);
      const delay = Number(process.env.PEF_MONITOR_SMOKE_DELAY_MS) || (DEMO ? 9000 : 2500);
      setTimeout(async () => {
        const shot = process.env.PEF_MONITOR_SCREENSHOT;
        if (shot) {
          const image = await win!.webContents.capturePage();
          require('node:fs').writeFileSync(path.resolve(shot), image.toPNG());
          result.screenshot = path.basename(shot);
        }
        result.appState = lastSnapshot?.appState ?? null;
        result.semaphore = lastSnapshot?.semaphore.color ?? null;
        clearTimeout(timeout);
        void win!.webContents
          .executeJavaScript(`document.querySelector('[data-testid="app-root"]')?.getAttribute('data-app-state') ?? null`)
          .then((s) => {
            result.domAppState = s;
            done(0);
          });
      }, delay);
    } catch (err) {
      result.error = String(err);
      clearTimeout(timeout);
      done(1);
    }
  });
}
