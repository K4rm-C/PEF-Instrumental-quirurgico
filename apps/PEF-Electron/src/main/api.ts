// HTTP client for the existing PEF APIs (no new auth mechanism):
// - AuthService /api/v1/auth/{login,refresh,logout} (Bearer tokens in the body, as the Android app);
// - SessionService /api/v1/sessions/stations/{station}/monitor[...] (ELECTRON-01, read-only).
// The access token lives only in memory. The refresh token is persisted by a SecretStore (encrypted with the
// OS key store in production). The password is used once for login and never stored or logged.
import type { BackendSession, LiveTicket, StationOverview } from '../shared/types';

export interface SecretStore {
  get(): string | null;
  set(value: string): void;
  clear(): void;
  readonly persistent: boolean;
}

export class MemorySecretStore implements SecretStore {
  private value: string | null = null;
  readonly persistent = false;
  get() {
    return this.value;
  }
  set(v: string) {
    this.value = v;
  }
  clear() {
    this.value = null;
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number, // 0 = network / timeout
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
  get isNetwork() {
    return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export interface ApiUrls {
  auth: string;
  session: string;
}

export interface ApiIdentity {
  email: string;
  roles: string[];
  locale: 'en' | 'es-MX';
}

export function userLocale(preferences: { locale?: string; language?: string } | null | undefined): 'en' | 'es-MX' {
  const value = (preferences?.locale || preferences?.language || '').toLowerCase().replace('_', '-');
  return value === 'es' || value === 'es-mx' ? 'es-MX' : 'en';
}

const TIMEOUT_MS = 8000;

export class PefApi {
  private accessToken: string | null = null;
  private refreshing: Promise<void> | null = null;
  identity: ApiIdentity | null = null;
  private identityFetchedAt = 0;

  constructor(
    private urls: ApiUrls,
    private readonly secrets: SecretStore,
    private readonly fetchImpl: FetchLike = (u, i) => fetch(u, i),
  ) {}

  setUrls(urls: ApiUrls): void {
    this.urls = urls;
  }

  hasCredentials(): boolean {
    return Boolean(this.accessToken || this.secrets.get());
  }

  private async raw(method: string, url: string, body?: unknown, token?: string | null): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      return await this.fetchImpl(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        redirect: 'error', // never follow a redirect with a token
      });
    } catch (err) {
      throw new ApiError(0, 'network', err instanceof Error ? err.message : 'network error');
    } finally {
      clearTimeout(timer);
    }
  }

  private static async fail(res: Response): Promise<ApiError> {
    let code = `http_${res.status}`;
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const data = (await res.json()) as { error?: string; message?: string };
      if (data?.error) code = String(data.error);
      if (data?.message) message = String(data.message);
    } catch {
      /* non-JSON error body (gateway) */
    }
    return new ApiError(res.status, code, message);
  }

  async login(email: string, password: string): Promise<ApiIdentity> {
    const res = await this.raw('POST', `${this.urls.auth}/api/v1/auth/login`, { email, password });
    if (!res.ok) throw await PefApi.fail(res);
    const data = (await res.json()) as {
      access_token: string;
      refresh_token: string;
      user?: { email?: string; roles?: string[]; ui_preferences?: { locale?: string; language?: string } };
    };
    const roles = data.user?.roles ?? [];
    if (!roles.includes('station_operator')) {
      // Do not keep a session the monitor cannot use (it only serves station operators).
      await this.raw('POST', `${this.urls.auth}/api/v1/auth/logout`, { refresh_token: data.refresh_token }, data.access_token).catch(() => undefined);
      throw new ApiError(403, 'not_station_operator', 'La cuenta no tiene el rol de Operador de estación.');
    }
    this.accessToken = data.access_token;
    this.secrets.set(data.refresh_token);
    this.identity = { email: data.user?.email ?? email, roles, locale: userLocale(data.user?.ui_preferences) };
    this.identityFetchedAt = Date.now();
    return this.identity;
  }

  /** Rotates the refresh token (AuthService revokes the presented one). Single flight. */
  async refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      const refreshToken = this.secrets.get();
      if (!refreshToken) throw new ApiError(401, 'no_credentials', 'Sin credenciales almacenadas.');
      const res = await this.raw('POST', `${this.urls.auth}/api/v1/auth/refresh`, { refresh_token: refreshToken });
      if (res.status === 401 || res.status === 403) {
        this.accessToken = null;
        this.secrets.clear();
        throw await PefApi.fail(res);
      }
      if (!res.ok) throw await PefApi.fail(res);
      const data = (await res.json()) as { access_token: string; refresh_token: string };
      this.accessToken = data.access_token;
      this.secrets.set(data.refresh_token);
      this.identityFetchedAt = 0;
    })();
    try {
      await this.refreshing;
    } finally {
      this.refreshing = null;
    }
  }

  async logout(): Promise<void> {
    const refreshToken = this.secrets.get();
    const access = this.accessToken;
    this.accessToken = null;
    this.secrets.clear();
    this.identity = null;
    this.identityFetchedAt = 0;
    if (access && refreshToken) {
      await this.raw('POST', `${this.urls.auth}/api/v1/auth/logout`, { refresh_token: refreshToken }, access).catch(() => undefined);
    }
  }

  async loadIdentity(): Promise<ApiIdentity | null> {
    try {
      const data = await this.authed<{ email?: string; roles?: string[]; ui_preferences?: { locale?: string; language?: string } }>('GET', `${this.urls.auth}/api/v1/auth/me`);
      this.identity = { email: data.email ?? '', roles: data.roles ?? [], locale: userLocale(data.ui_preferences) };
      this.identityFetchedAt = Date.now();
    } catch {
      /* informative only */
      this.identityFetchedAt = Date.now();
    }
    return this.identity;
  }

  /** Authenticated call; one transparent refresh on 401, then the 401 surfaces (revocation). */
  private async authed<T>(method: string, url: string, body?: unknown): Promise<T> {
    if (!this.accessToken) await this.refresh();
    let res = await this.raw(method, url, body, this.accessToken);
    if (res.status === 401) {
      await this.refresh();
      res = await this.raw(method, url, body, this.accessToken);
    }
    if (!res.ok) throw await PefApi.fail(res);
    return (await res.json()) as T;
  }

  async stationOverview(stationId: string): Promise<StationOverview> {
    const overview = await this.authed<StationOverview>('GET', `${this.urls.session}/api/v1/sessions/stations/${encodeURIComponent(stationId)}/monitor`);
    if (Date.now() - this.identityFetchedAt > 60_000) await this.loadIdentity();
    return overview;
  }

  async sessionMonitor(stationId: string, sessionId: string): Promise<BackendSession> {
    const data = await this.authed<{ session: BackendSession }>(
      'GET',
      `${this.urls.session}/api/v1/sessions/stations/${encodeURIComponent(stationId)}/monitor/${encodeURIComponent(sessionId)}`,
    );
    return data.session;
  }

  liveTicket(stationId: string, sessionId: string): Promise<LiveTicket> {
    return this.authed(
      'POST',
      `${this.urls.session}/api/v1/sessions/stations/${encodeURIComponent(stationId)}/monitor/${encodeURIComponent(sessionId)}/live-ticket`,
    );
  }
}
