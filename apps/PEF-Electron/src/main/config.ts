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

/**
 * http/ws only for loopback (development stack on this PC). Anything reaching another host must be https/wss,
 * whose certificate Chromium/Node validate (no override anywhere in this app). PEF_MONITOR_ALLOW_INSECURE_LAN=1
 * relaxes it for a closed lab network only; it is logged at every start.
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
  if (url.protocol === plain && !LOOPBACK.has(url.hostname) && !allowInsecureLan) {
    throw new ConfigError(`${raw}: fuera de este equipo solo se permite ${secure.replace(':', '')} (certificado validado)`);
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
