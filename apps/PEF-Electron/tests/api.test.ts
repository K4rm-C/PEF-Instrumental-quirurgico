import { describe, expect, it } from 'vitest';
import { ApiError, MemorySecretStore, PefApi, userLocale, type FetchLike } from '../src/main/api';
import { STATION, SID, overview } from './fixtures';

type Call = { url: string; init: RequestInit };

function fakeFetch(routes: (call: Call, n: number) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fn: FetchLike = async (url, init) => {
    const call = { url, init };
    calls.push(call);
    return routes(call, calls.length);
  };
  return { fn, calls };
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const URLS = { auth: 'https://auth.test', session: 'https://session.test' };
const loginBody = (roles: string[]) => ({ access_token: 'acc1', refresh_token: 'ref1', token_type: 'Bearer', expires_in: 900, user: { email: 'op@x', roles } });

describe('PefApi', () => {
  it('uses the account locale, including the legacy language alias', () => {
    expect(userLocale({ locale: 'es-MX' })).toBe('es-MX');
    expect(userLocale({ language: 'es' })).toBe('es-MX');
    expect(userLocale({ locale: 'en' })).toBe('en');
  });

  it('reads the locale supplied by AuthService at login', async () => {
    const { fn } = fakeFetch(() => json(200, {
      ...loginBody(['station_operator']),
      user: { email: 'op@x', roles: ['station_operator'], ui_preferences: { locale: 'en' } },
    }));
    const api = new PefApi(URLS, new MemorySecretStore(), fn);
    expect((await api.login('op@x', 'pw')).locale).toBe('en');
  });
  it('logs in an operator, keeps the access token in memory and stores only the refresh token', async () => {
    const secrets = new MemorySecretStore();
    const { fn, calls } = fakeFetch(() => json(200, loginBody(['station_operator'])));
    const api = new PefApi(URLS, secrets, fn);
    const id = await api.login('op@x', 'pw');
    expect(id.roles).toContain('station_operator');
    expect(secrets.get()).toBe('ref1');
    expect(calls[0].url).toBe('https://auth.test/api/v1/auth/login');
    expect(calls[0].init.redirect).toBe('error');
  });

  it('refuses (and logs out) an account that is not a station operator', async () => {
    const secrets = new MemorySecretStore();
    const { fn, calls } = fakeFetch((c) => (c.url.endsWith('/login') ? json(200, loginBody(['spd_supervisor'])) : new Response(null, { status: 204 })));
    const api = new PefApi(URLS, secrets, fn);
    await expect(api.login('spd@x', 'pw')).rejects.toMatchObject({ code: 'not_station_operator' });
    expect(secrets.get()).toBeNull();
    expect(calls.some((c) => c.url.endsWith('/logout'))).toBe(true);
  });

  it('sends Bearer to SessionService and refreshes once on 401 (rotating the refresh token)', async () => {
    const secrets = new MemorySecretStore();
    secrets.set('ref0');
    let station = 0;
    const { fn, calls } = fakeFetch((c) => {
      if (c.url.endsWith('/refresh')) {
        const body = JSON.parse(String(c.init.body));
        return json(200, { access_token: `acc-${body.refresh_token}`, refresh_token: `${body.refresh_token}+` });
      }
      if (c.url.endsWith('/me')) return json(200, { email: 'op@x', roles: ['station_operator'], ui_preferences: { locale: 'en' } });
      station += 1;
      return station === 2 ? json(401, { error: 'unauthorized' }) : json(200, overview('idle'));
    });
    const api = new PefApi(URLS, secrets, fn);
    await api.stationOverview(STATION); // no access token yet → refresh first
    await api.stationOverview(STATION); // 401 → refresh → retry
    const sessionCalls = calls.filter((c) => c.url.includes('/monitor'));
    expect(sessionCalls[0].url).toBe(`https://session.test/api/v1/sessions/stations/${STATION}/monitor`);
    expect((sessionCalls[0].init.headers as Record<string, string>).Authorization).toBe('Bearer acc-ref0');
    expect((sessionCalls[2].init.headers as Record<string, string>).Authorization).toBe('Bearer acc-ref0+');
    expect(secrets.get()).toBe('ref0++');
  });

  it('revocation: a refused refresh clears the stored credential and surfaces 401', async () => {
    const secrets = new MemorySecretStore();
    secrets.set('revoked');
    const { fn } = fakeFetch((c) => (c.url.endsWith('/refresh') ? json(401, { error: 'unauthorized', message: 'Token is revoked' }) : json(200, {})));
    const api = new PefApi(URLS, secrets, fn);
    await expect(api.stationOverview(STATION)).rejects.toMatchObject({ status: 401 });
    expect(secrets.get()).toBeNull();
    expect(api.hasCredentials()).toBe(false);
  });

  it('maps network failures to ApiError(0) and server envelopes to codes', async () => {
    const secrets = new MemorySecretStore();
    secrets.set('r');
    const down = new PefApi(URLS, secrets, async () => {
      throw new TypeError('fetch failed');
    });
    await expect(down.stationOverview(STATION)).rejects.toMatchObject({ status: 0, isNetwork: true });
    const { fn } = fakeFetch((c) =>
      c.url.endsWith('/refresh') ? json(200, { access_token: 'a', refresh_token: 'r' }) : json(409, { error: 'live_not_available', message: 'x' }),
    );
    const api = new PefApi(URLS, secrets, fn);
    const err = await api.liveTicket(STATION, SID).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: 'live_not_available' });
  });

  it('logout revokes server side and forgets everything locally', async () => {
    const secrets = new MemorySecretStore();
    const { fn, calls } = fakeFetch((c) => (c.url.endsWith('/login') ? json(200, loginBody(['station_operator'])) : new Response(null, { status: 204 })));
    const api = new PefApi(URLS, secrets, fn);
    await api.login('op@x', 'pw');
    await api.logout();
    const logout = calls.find((c) => c.url.endsWith('/logout'))!;
    expect(JSON.parse(String(logout.init.body))).toEqual({ refresh_token: 'ref1' });
    expect(api.hasCredentials()).toBe(false);
  });
});
