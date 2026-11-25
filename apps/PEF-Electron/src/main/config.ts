// Local station configuration (no secrets): userData/station-config.json.
// Credentials are NOT here: the refresh token lives encrypted in credentials.ts (Windows DPAPI via safeStorage).
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { SaveConfigInput } from '../shared/ipc';

export interface StationConfig {
  apiBaseUrl: string; // API Gateway (recommended) or AuthService/SessionService host
  authBaseUrl: string; // '' = apiBaseUrl
  sessionBaseUrl: string; // '' = apiBaseUrl
  visionWsUrl: string; // '' = server hint, else derived from apiBaseUrl (/ws)
  stationId: string;
  autostart: boolean;
  startHidden: boolean;
  hideWhenIdle: boolean;
}

export const EMPTY_CONFIG: StationConfig = {
  apiBaseUrl: '',
  authBaseUrl: '',
  sessionBaseUrl: '',
  visionWsUrl: '',
  stationId: '',
  autostart: false,
  startHidden: false,
  hideWhenIdle: false,
};

export class ConfigError extends Error {}

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]', '::1']);

/** Loopback hostnames used by the local Docker/dev stack. */
export function isLoopbackHost(hostname: string): boolean {
  return LOOPBACK.has(hostname.toLowerCase());
}

/**
 * RFC1918 + link-local IPv4 (lab PEF demos mount the stack on HOST_LAN_IP, not only localhost).
 * Hostnames that are not literal IPs still require https/wss (or PEF_MONITOR_ALLOW_INSECURE_LAN=1).
 */
export function isPrivateLanHost(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (isLoopbackHost(h) || isLoopbackHost(hostname)) return true;
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (!m) return false;
  const octets = m.slice(1).map((x) => Number(x));
  if (octets.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = octets;
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 169 && b === 254) return true;
  return false;
}

/**
 * http/ws allowed for loopback and private LAN IPs (demo/lab on HOST_LAN_IP).
 * Public or named hosts must use https/wss (certificate validated), unless
 * PEF_MONITOR_ALLOW_INSECURE_LAN=1 relaxes that (logged at every start).
 */
export function validateUrl(raw: string, kind: 'http' | 'ws', allowInsecureLan = false): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ConfigError(`URL inválida: ${raw}`);
  }
  const secure = kind === 'http' ? 'https:' : 'wss:';
  const plain = kind === 'http' ? 'http:' : 'ws:';
  if (url.protocol !== secure && url.protocol !== plain) {
    throw new ConfigError(`Esquema no permitido en ${raw} (use ${secure.replace(':', '')})`);
  }
  if (url.username || url.password) throw new ConfigError('La URL no debe incluir credenciales');
  if (url.search || url.hash) throw new ConfigError('La URL no debe incluir parámetros');
  const plainOk = isPrivateLanHost(url.hostname) || allowInsecureLan;
  if (url.protocol === plain && !plainOk) {
    throw new ConfigError(
      `${raw}: fuera de loopback/LAN privada solo se permite ${secure.replace(':', '')} (certificado validado), `
        + 'o PEF_MONITOR_ALLOW_INSECURE_LAN=1 en laboratorio',
    );
  }
  return url.toString().replace(/\/+$/, '');
}

export function normalizeConfig(input: SaveConfigInput, previous: StationConfig, allowInsecureLan = false): StationConfig {
  const apiBaseUrl = validateUrl(input.apiBaseUrl, 'http', allowInsecureLan);
  return {
    ...previous,
    apiBaseUrl,
    authBaseUrl: input.authBaseUrl ? validateUrl(input.authBaseUrl, 'http', allowInsecureLan) : '',
    sessionBaseUrl: input.sessionBaseUrl ? validateUrl(input.sessionBaseUrl, 'http', allowInsecureLan) : '',
    visionWsUrl: input.visionWsUrl ? validateUrl(input.visionWsUrl, 'ws', allowInsecureLan) : '',
    stationId: input.stationId,
    startHidden: input.startHidden ?? previous.startHidden,
    hideWhenIdle: input.hideWhenIdle ?? previous.hideWhenIdle,
  };
}

export function isConfigured(cfg: StationConfig): boolean {
  return Boolean(cfg.apiBaseUrl && cfg.stationId);
}

export function resolvedUrls(cfg: StationConfig, serverWsHint: string | null = null) {
  const auth = cfg.authBaseUrl || cfg.apiBaseUrl;
  const session = cfg.sessionBaseUrl || cfg.apiBaseUrl;
  let ws = cfg.visionWsUrl || serverWsHint || '';
  if (!ws && cfg.apiBaseUrl) {
    const u = new URL(cfg.apiBaseUrl);
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    u.pathname = '/ws';
    ws = u.toString();
  }
  return { auth, session, ws };
}

function parseStored(raw: unknown): StationConfig {
  if (typeof raw !== 'object' || raw === null) return { ...EMPTY_CONFIG };
  const r = raw as Record<string, unknown>;
  const s = (k: string) => (typeof r[k] === 'string' ? (r[k] as string) : '');
  const b = (k: string) => r[k] === true;
  return {
    apiBaseUrl: s('apiBaseUrl'),
    authBaseUrl: s('authBaseUrl'),
    sessionBaseUrl: s('sessionBaseUrl'),
    visionWsUrl: s('visionWsUrl'),
    stationId: s('stationId'),
    autostart: b('autostart'),
    startHidden: b('startHidden'),
    hideWhenIdle: b('hideWhenIdle'),
  };
}

export class ConfigStore {
  private readonly file: string;

  constructor(dir: string) {
    this.file = path.join(dir, 'station-config.json');
  }

  load(): StationConfig {
    try {
      return parseStored(JSON.parse(fs.readFileSync(this.file, 'utf8')));
    } catch {
      return { ...EMPTY_CONFIG };
    }
  }

  save(cfg: StationConfig): void {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }
}
