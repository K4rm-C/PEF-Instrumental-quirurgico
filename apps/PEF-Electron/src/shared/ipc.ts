// IPC contract. The renderer never talks to the network, the file system or Node: it can only call these
// channels through the preload bridge, and the main process validates every payload before using it.
// NOTE: src/preload/preload.ts repeats these channel names literally (a sandboxed preload cannot require local
// modules); tests/ipc.test.ts checks both lists stay identical.

export const IPC = {
  getSnapshot: 'pef:get-snapshot',
  getConfig: 'pef:get-config',
  saveConfig: 'pef:save-config',
  login: 'pef:login',
  logout: 'pef:logout',
  setAutostart: 'pef:set-autostart',
  hideToTray: 'pef:hide-to-tray',
  reportLocalCameras: 'pef:report-local-cameras',
  retry: 'pef:retry',
  snapshotEvent: 'pef:snapshot',
} as const;

export type IpcResult<T = undefined> = { ok: true; value?: T } | { ok: false; error: string };

export interface SaveConfigInput {
  apiBaseUrl: string;
  authBaseUrl?: string;
  sessionBaseUrl?: string;
  visionWsUrl?: string;
  stationId: string;
  startHidden?: boolean;
  hideWhenIdle?: boolean;
}

export interface LoginInput {
  email: string;
  password: string;
}

export class IpcValidationError extends Error {}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}

function onlyKeys(obj: Record<string, unknown>, allowed: string[]): void {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) throw new IpcValidationError(`Campo no permitido: ${key}`);
  }
}

function str(value: unknown, field: string, { min = 0, max = 512 } = {}): string {
  if (typeof value !== 'string') throw new IpcValidationError(`${field} debe ser texto`);
  const trimmed = value.trim();
  if (trimmed.length < min || trimmed.length > max) throw new IpcValidationError(`${field} tiene una longitud inválida`);
  return trimmed;
}

function optStr(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null || value === '') return undefined;
  return str(value, field, { max: 512 });
}

function optBool(value: unknown, field: string): boolean | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'boolean') throw new IpcValidationError(`${field} debe ser booleano`);
  return value;
}

export function validateSaveConfig(payload: unknown): SaveConfigInput {
  if (!isPlainObject(payload)) throw new IpcValidationError('Configuración inválida');
  onlyKeys(payload, ['apiBaseUrl', 'authBaseUrl', 'sessionBaseUrl', 'visionWsUrl', 'stationId', 'startHidden', 'hideWhenIdle']);
  const stationId = str(payload.stationId, 'stationId', { min: 36, max: 36 });
  if (!UUID_RE.test(stationId)) throw new IpcValidationError('El identificador de estación debe ser un UUID');
  return {
    apiBaseUrl: str(payload.apiBaseUrl, 'apiBaseUrl', { min: 8, max: 512 }),
    authBaseUrl: optStr(payload.authBaseUrl, 'authBaseUrl'),
    sessionBaseUrl: optStr(payload.sessionBaseUrl, 'sessionBaseUrl'),
    visionWsUrl: optStr(payload.visionWsUrl, 'visionWsUrl'),
    stationId: stationId.toLowerCase(),
    startHidden: optBool(payload.startHidden, 'startHidden'),
    hideWhenIdle: optBool(payload.hideWhenIdle, 'hideWhenIdle'),
  };
}

export function validateLogin(payload: unknown): LoginInput {
  if (!isPlainObject(payload)) throw new IpcValidationError('Credenciales inválidas');
  onlyKeys(payload, ['email', 'password']);
  const email = str(payload.email, 'email', { min: 3, max: 254 }).toLowerCase();
  if (!EMAIL_RE.test(email)) throw new IpcValidationError('Correo inválido');
  if (typeof payload.password !== 'string' || payload.password.length < 1 || payload.password.length > 256) {
    throw new IpcValidationError('Contraseña inválida');
  }
  return { email, password: payload.password };
}

export function validateBoolean(payload: unknown, field: string): boolean {
  if (typeof payload !== 'boolean') throw new IpcValidationError(`${field} debe ser booleano`);
  return payload;
}

export function validateLocalCameras(payload: unknown): number {
  if (!isPlainObject(payload)) throw new IpcValidationError('Reporte de cámaras inválido');
  onlyKeys(payload, ['videoInputs']);
  const n = payload.videoInputs;
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 0 || n > 32) throw new IpcValidationError('videoInputs inválido');
  return n;
}

export function validateNoArgs(payload: unknown): void {
  if (payload !== undefined) throw new IpcValidationError('Este canal no acepta argumentos');
}

/** Only our own page, loaded from the app's privileged protocol, may call the main process. */
export const APP_ORIGIN = 'pef-app://monitor';

export function isTrustedSenderUrl(url: string | undefined | null): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'pef-app:' && parsed.host === 'monitor';
  } catch {
    return false;
  }
}
