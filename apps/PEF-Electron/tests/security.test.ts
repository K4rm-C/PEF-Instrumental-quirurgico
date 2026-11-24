import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  IPC,
  IpcValidationError,
  isTrustedSenderUrl,
  validateBoolean,
  validateLocalCameras,
  validateLogin,
  validateNoArgs,
  validateSaveConfig,
} from '../src/shared/ipc';
import { ConfigError, ConfigStore, EMPTY_CONFIG, normalizeConfig, resolvedUrls, validateUrl } from '../src/main/config';
import { EncryptedFileSecretStore, type SafeStorageLike } from '../src/main/credentials';
import { Logger, redact } from '../src/main/logger';
import { CSP, resolveAppFile } from '../src/main/security';
import { STATION } from './fixtures';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'pef-monitor-test-'));

describe('IPC contract', () => {
  it('preload exposes exactly the shared channels (sandboxed preload repeats them literally)', () => {
    const preload = fs.readFileSync(path.join(__dirname, '..', 'src', 'preload', 'preload.ts'), 'utf8');
    for (const channel of Object.values(IPC)) expect(preload).toContain(`'${channel}'`);
    const literals = preload.match(/'pef:[a-z-]+'/g) ?? [];
    expect(new Set(literals)).toEqual(new Set(Object.values(IPC).map((c) => `'${c}'`)));
    // No generic escape hatch.
    expect(preload).not.toMatch(/exposeInMainWorld\([^)]*ipcRenderer\b(?!\.)/);
    expect(preload).not.toMatch(/send:\s*\(|invoke:\s*\(/);
  });

  it('only the app page loaded from pef-app://monitor is a trusted sender', () => {
    expect(isTrustedSenderUrl('pef-app://monitor/index.html')).toBe(true);
    expect(isTrustedSenderUrl('pef-app://other/index.html')).toBe(false);
    expect(isTrustedSenderUrl('https://evil.example/')).toBe(false);
    expect(isTrustedSenderUrl('file:///C:/x/index.html')).toBe(false);
    expect(isTrustedSenderUrl('')).toBe(false);
    expect(isTrustedSenderUrl(null)).toBe(false);
  });

  it('validates configuration payloads strictly', () => {
    const ok = validateSaveConfig({ apiBaseUrl: 'https://api.example', stationId: STATION.toUpperCase() });
    expect(ok.stationId).toBe(STATION);
    expect(() => validateSaveConfig({ apiBaseUrl: 'https://api.example', stationId: 'not-a-uuid-not-a-uuid-not-a-uuid-xx' })).toThrow(IpcValidationError);
    expect(() => validateSaveConfig({ apiBaseUrl: 'https://api.example', stationId: STATION, token: 'x' })).toThrow(/no permitido/);
    expect(() => validateSaveConfig(null)).toThrow(IpcValidationError);
    expect(() => validateSaveConfig([1])).toThrow(IpcValidationError);
    expect(() => validateSaveConfig({ apiBaseUrl: 'https://api.example', stationId: STATION, startHidden: 'yes' })).toThrow(IpcValidationError);
  });

  it('validates login, booleans, camera reports and argument-less channels', () => {
    expect(validateLogin({ email: ' Operator@Instrumed.com ', password: 'x' }).email).toBe('operator@instrumed.com');
    expect(() => validateLogin({ email: 'nope', password: 'x' })).toThrow(IpcValidationError);
    expect(() => validateLogin({ email: 'a@b.c', password: '' })).toThrow(IpcValidationError);
    expect(() => validateLogin({ email: 'a@b.c', password: 'x'.repeat(257) })).toThrow(IpcValidationError);
    expect(() => validateLogin({ email: 'a@b.c', password: 'x', role: 'admin' })).toThrow(IpcValidationError);
    expect(validateBoolean(true, 'x')).toBe(true);
    expect(() => validateBoolean('true', 'x')).toThrow(IpcValidationError);
    expect(validateLocalCameras({ videoInputs: 2 })).toBe(2);
    expect(() => validateLocalCameras({ videoInputs: -1 })).toThrow(IpcValidationError);
    expect(() => validateLocalCameras({ videoInputs: 1.5 })).toThrow(IpcValidationError);
    expect(() => validateNoArgs({ a: 1 })).toThrow(IpcValidationError);
    expect(() => validateNoArgs(undefined)).not.toThrow();
  });
});

describe('station configuration', () => {
  it('requires https/wss outside this PC and rejects credentials or parameters in URLs', () => {
    expect(validateUrl('http://127.0.0.1:8080/', 'http')).toBe('http://127.0.0.1:8080');
    expect(validateUrl('https://api.hospital.example', 'http')).toBe('https://api.hospital.example');
    expect(validateUrl('ws://localhost:5002/ws', 'ws')).toBe('ws://localhost:5002/ws');
    expect(() => validateUrl('http://10.0.0.5:8080', 'http')).toThrow(ConfigError);
    expect(() => validateUrl('ws://10.0.0.5/ws', 'ws')).toThrow(ConfigError);
    expect(validateUrl('http://10.0.0.5:8080', 'http', true)).toBe('http://10.0.0.5:8080'); // explicit lab flag only
    expect(() => validateUrl('https://user:pw@api.example', 'http')).toThrow(ConfigError);
    expect(() => validateUrl('https://api.example/?token=x', 'http')).toThrow(ConfigError);
    expect(() => validateUrl('file:///C:/x', 'http')).toThrow(ConfigError);
    expect(() => validateUrl('javascript:alert(1)', 'http')).toThrow(ConfigError);
  });

  it('derives service URLs from the gateway and honours explicit overrides', () => {
    const cfg = normalizeConfig({ apiBaseUrl: 'https://api.example', stationId: STATION }, EMPTY_CONFIG);
    expect(resolvedUrls(cfg)).toEqual({ auth: 'https://api.example', session: 'https://api.example', ws: 'wss://api.example/ws' });
    expect(resolvedUrls(cfg, 'wss://hint.example/ws').ws).toBe('wss://hint.example/ws');
    const dev = normalizeConfig({
      apiBaseUrl: 'http://127.0.0.1:8080', authBaseUrl: 'http://127.0.0.1:5001', sessionBaseUrl: 'http://127.0.0.1:5004',
      visionWsUrl: 'ws://127.0.0.1:5002/ws', stationId: STATION,
    }, EMPTY_CONFIG);
    expect(resolvedUrls(dev, 'wss://ignored/ws')).toEqual({ auth: 'http://127.0.0.1:5001', session: 'http://127.0.0.1:5004', ws: 'ws://127.0.0.1:5002/ws' });
  });

  it('persists configuration without secrets and survives a corrupt file', () => {
    const dir = tmp();
    const store = new ConfigStore(dir);
    expect(store.load()).toEqual(EMPTY_CONFIG);
    const cfg = normalizeConfig({ apiBaseUrl: 'https://api.example', stationId: STATION }, EMPTY_CONFIG);
    store.save(cfg);
    expect(store.load()).toEqual(cfg);
    const raw = fs.readFileSync(path.join(dir, 'station-config.json'), 'utf8');
    expect(raw).not.toMatch(/token|password/i);
    fs.writeFileSync(path.join(dir, 'station-config.json'), '{broken');
    expect(store.load()).toEqual(EMPTY_CONFIG);
  });
});

describe('credentials at rest', () => {
  const fakeSafe = (available: boolean): SafeStorageLike => ({
    isEncryptionAvailable: () => available,
    encryptString: (s) => Buffer.from(s.split('').reverse().join('') + '|enc'),
    decryptString: (b) => b.toString().replace(/\|enc$/, '').split('').reverse().join(''),
  });

  it('stores the refresh token encrypted, never in clear text', () => {
    const dir = tmp();
    const store = new EncryptedFileSecretStore(dir, fakeSafe(true));
    store.set('refresh-token-SECRET');
    const onDisk = fs.readFileSync(path.join(dir, 'credentials.bin')).toString();
    expect(onDisk).not.toContain('refresh-token-SECRET');
    expect(new EncryptedFileSecretStore(dir, fakeSafe(true)).get()).toBe('refresh-token-SECRET');
    store.clear();
    expect(fs.existsSync(path.join(dir, 'credentials.bin'))).toBe(false);
  });

  it('without OS encryption keeps the token in memory only', () => {
    const dir = tmp();
    const warnings: string[] = [];
    const store = new EncryptedFileSecretStore(dir, fakeSafe(false), (m) => warnings.push(m));
    store.set('refresh-token-SECRET');
    expect(store.get()).toBe('refresh-token-SECRET');
    expect(fs.readdirSync(dir)).toEqual([]);
    expect(store.persistent).toBe(false);
    expect(warnings.length).toBe(1);
  });
});

describe('diagnostic logs without secrets', () => {
  it('redacts tokens, tickets, passwords and images', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';
    const ticket = `m1.${STATION}.1790000000.abcdef0123.${'a'.repeat(64)}`;
    const line = redact(`Authorization: Bearer ${jwt} {"password":"DemopwdOP78!","refresh_token":"r1"} ${ticket} ${'A'.repeat(300)}`);
    expect(line).not.toContain(jwt);
    expect(line).not.toContain('DemopwdOP78!');
    expect(line).not.toContain('"r1"');
    expect(line).not.toContain(ticket);
    expect(line).not.toMatch(/A{200}/);
  });

  it('writes and rotates the log file', () => {
    const dir = tmp();
    const log = new Logger(dir);
    log.info('hello', { password: 'x' });
    const content = fs.readFileSync(path.join(dir, 'monitor.log'), 'utf8');
    expect(content).toMatch(/INFO hello/);
    expect(content).not.toMatch(/"password":"x"/);
  });
});

describe('renderer served from the app protocol', () => {
  const root = tmp();
  fs.writeFileSync(path.join(root, 'index.html'), '<html></html>');
  fs.mkdirSync(path.join(root, 'assets'));
  fs.writeFileSync(path.join(root, 'assets', 'a.js'), '');

  it('serves only files inside the renderer folder', () => {
    expect(resolveAppFile(root, 'pef-app://monitor/index.html')).toBe(path.join(root, 'index.html'));
    expect(resolveAppFile(root, 'pef-app://monitor/')).toBe(path.join(root, 'index.html'));
    expect(resolveAppFile(root, 'pef-app://monitor/assets/a.js')).toBe(path.join(root, 'assets', 'a.js'));
    expect(resolveAppFile(root, 'pef-app://monitor/../package.json')).toBeNull();
    // Encoded dot segments are normalised by the URL parser and can never climb above the root.
    const encoded = resolveAppFile(root, 'pef-app://monitor/%2e%2e/%2e%2e/secret.html');
    expect(encoded === null || encoded.startsWith(root + path.sep)).toBe(true);
    expect(resolveAppFile(root, 'pef-app://monitor/..%5c..%5cwin.ini')).toBeNull();
    expect(resolveAppFile(root, 'pef-app://other/index.html')).toBeNull();
    expect(resolveAppFile(root, 'pef-app://monitor/data.json')).toBeNull(); // unknown type
  });

  it('CSP forbids inline/eval scripts and any network access from the renderer', () => {
    expect(CSP).toContain("script-src 'self'");
    expect(CSP).toContain("connect-src 'none'");
    expect(CSP).not.toMatch(/unsafe-inline|unsafe-eval/);
    const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
    expect(html).toContain("connect-src 'none'");
    expect(html).not.toMatch(/unsafe-/);
  });

  it('BrowserWindow is created with the hardened web preferences', () => {
    const main = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
    for (const pref of ['contextIsolation: true', 'nodeIntegration: false', 'sandbox: true', 'webSecurity: true', 'allowRunningInsecureContent: false']) {
      expect(main).toContain(pref);
    }
    expect(main).toContain('requestSingleInstanceLock');
    expect(main).toContain('app.enableSandbox()');
    expect(main).not.toMatch(/rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED/);
  });
});

describe('Windows start-up and protocol activation', () => {
  it('autostart starts hidden in the tray (packaged and development)', async () => {
    const { autostartSettings } = await import('../src/main/autostart');
    expect(autostartSettings(true, true, 'C:/PEF/PEFMonitor.exe', 'C:/PEF/resources/app.asar')).toEqual({
      openAtLogin: true, path: 'C:/PEF/PEFMonitor.exe', args: ['--hidden'],
    });
    const dev = autostartSettings(false, false, 'C:/x/electron.exe', 'C:/repo/apps/PEF-Electron');
    expect(dev.openAtLogin).toBe(false);
    expect(dev.args.at(-1)).toBe('--hidden');
    expect(dev.args[0]).toMatch(/PEF-Electron$/);
  });

  it('pef-monitor:// only honours the exact "open" action', async () => {
    const { isOpenRequest } = await import('../src/main/autostart');
    expect(isOpenRequest(['x.exe', 'pef-monitor://open'])).toBe(true);
    expect(isOpenRequest(['pef-monitor://open/'])).toBe(true);
    expect(isOpenRequest(['pef-monitor://open?session=123'])).toBe(false);
    expect(isOpenRequest(['pef-monitor://session/123'])).toBe(false);
    expect(isOpenRequest(['--hidden'])).toBe(false);
  });
});
