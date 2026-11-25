import { afterEach, describe, expect, it } from 'vitest';
import { ApiError } from '../src/main/api';
import { VisionObserver, type ObserverStatus, type WsLike } from '../src/main/visionObserver';
import type { LiveTicket, MonitorStateMessage } from '../src/shared/types';
import { SID, SID2, stateMsg } from './fixtures';
import { FakeVisionWorker, waitFor } from './wsServer';

const workers: FakeVisionWorker[] = [];
const observers: VisionObserver[] = [];
afterEach(async () => {
  observers.splice(0).forEach((o) => o.stop());
  await Promise.all(workers.splice(0).map((w) => w.stop()));
});

async function worker(): Promise<FakeVisionWorker> {
  const w = new FakeVisionWorker();
  await w.start();
  workers.push(w);
  return w;
}

function observer(w: FakeVisionWorker, over: { getTicket?: () => Promise<LiveTicket>; ttl?: number; watchdogMs?: number } = {}) {
  let n = 0;
  const statuses: ObserverStatus[] = [];
  const states: MonitorStateMessage[] = [];
  const tickets: string[] = [];
  const obs = new VisionObserver({
    sessionId: SID,
    getTicket:
      over.getTicket ??
      (async () => {
        const t = `t${++n}`;
        w.valid.add(t);
        tickets.push(t);
        return { session_id: SID, ticket: t, ws_url: null, expires_at: 0, ttl_s: over.ttl ?? 60, scope: 'observe' };
      }),
    resolveUrl: () => w.url,
    wsFactory: (url) => new WebSocket(url) as unknown as WsLike,
    onState: (m) => states.push(m),
    onStatus: (s) => statuses.push(s),
    backoffMs: [50, 100, 200],
    watchdogMs: over.watchdogMs ?? 3000,
  });
  observers.push(obs);
  return { obs, statuses, states, tickets };
}

describe('VisionObserver (real WebSocket)', () => {
  it('subscribes with the ticket and receives states of its session', async () => {
    const w = await worker();
    const { obs, states, statuses } = observer(w);
    obs.start();
    await waitFor(() => obs.status === 'connected');
    expect(w.subscriptions[0]).toMatchObject({ sessionId: SID, ticket: 't1' });
    w.broadcast(stateMsg({ kelly: 4 }, 1));
    await waitFor(() => states.length === 1);
    expect(states[0].counts_by_family[0]).toEqual({ family_code: 'kelly', detected_quantity: 4 });
    expect(statuses[0]).toBe('connecting');
  });

  it('drops messages of another session', async () => {
    const w = await worker();
    const { obs, states } = observer(w);
    obs.start();
    await waitFor(() => obs.status === 'connected');
    w.broadcast({ ...stateMsg({ kelly: 4 }, 1), session_id: SID2 });
    w.broadcast(stateMsg({ kelly: 3 }, 2));
    await waitFor(() => states.length === 1);
    expect(states[0].session_id).toBe(SID);
  });

  it('renews the ticket every ttl/3 on the same socket', async () => {
    const w = await worker();
    const { obs } = observer(w, { ttl: 3 }); // renew every 1 s
    obs.start();
    await waitFor(() => w.renewals.length >= 1, 4000);
    expect(w.renewals[0]).toBe('t2');
    expect(w.subscriptions.length).toBe(1);
  });

  it('reconnects with a NEW ticket after the worker restarts', async () => {
    const w = await worker();
    const port = w.port;
    const { obs, statuses, tickets } = observer(w);
    obs.start();
    await waitFor(() => obs.status === 'connected');
    await w.stop();
    await waitFor(() => statuses.includes('reconnecting'));
    await w.start(port); // VisionWorker back on the same port
    await waitFor(() => obs.status === 'connected', 6000);
    expect(tickets.length).toBeGreaterThanOrEqual(2);
    expect(w.subscriptions.at(-1)!.ticket).not.toBe('t1');
  });

  it('expired ticket: asks for a fresh one; repeated refusals are final', async () => {
    const w = await worker();
    const { obs, tickets } = observer(w);
    obs.start();
    await waitFor(() => obs.status === 'connected');
    w.sendError('session_expired');
    await waitFor(() => tickets.length >= 2 && obs.status === 'connected', 4000);
    // From now on the worker refuses every ticket (e.g. secret rotated): give up, do not loop forever.
    w.valid.clear();
    const origAdd = w.valid.add.bind(w.valid);
    w.valid.add = () => w.valid; // tickets are no longer accepted
    w.sendError('unauthorized');
    await waitFor(() => obs.status === 'unauthorized', 6000);
    w.valid.add = origAdd;
  });

  it('revocation: the ticket endpoint refusing (401/403/404) stops without retrying', async () => {
    const w = await worker();
    let calls = 0;
    const { obs, statuses } = observer(w, {
      getTicket: async () => {
        calls += 1;
        throw new ApiError(401, 'unauthorized', 'revoked');
      },
    });
    obs.start();
    await waitFor(() => obs.status === 'unauthorized');
    await new Promise((r) => setTimeout(r, 300));
    expect(calls).toBe(1);
    expect(statuses).toContain('unauthorized');
    expect(w.subscriptions.length).toBe(0);
  });

  it('renewal failing because SessionService is unreachable keeps the SAME socket (no leaked connections)', async () => {
    const w = await worker();
    let n = 0;
    let down = false;
    const { obs } = observer(w, {
      ttl: 3, // renew every 1 s
      watchdogMs: 8000, // this fake worker sends no periodic heartbeat
      getTicket: async () => {
        if (down) throw new ApiError(0, 'network', 'ECONNREFUSED');
        const t = `r${++n}`;
        w.valid.add(t);
        return { session_id: SID, ticket: t, ws_url: null, expires_at: 0, ttl_s: 3, scope: 'observe' };
      },
    });
    obs.start();
    await waitFor(() => obs.status === 'connected');
    down = true;
    await new Promise((r) => setTimeout(r, 2500)); // at least two failed renewals
    expect(obs.status).toBe('connected');
    expect(w.clients.size).toBe(1);
    expect(w.subscriptions.length).toBe(1);
    down = false;
    await waitFor(() => w.renewals.length >= 1, 4000);
    expect(w.clients.size).toBe(1);
  });

  it('session no longer in progress (409) stops as unavailable', async () => {
    const w = await worker();
    const { obs } = observer(w, { getTicket: async () => Promise.reject(new ApiError(409, 'live_not_available', 'x')) });
    obs.start();
    await waitFor(() => obs.status === 'unavailable');
  });

  it('watchdog: a silent link is treated as lost and re-established', async () => {
    const w = await worker();
    const { obs, statuses } = observer(w, { watchdogMs: 400 });
    obs.start();
    await waitFor(() => obs.status === 'connected');
    // The fake worker sends nothing after subscribing → watchdog fires.
    await waitFor(() => statuses.filter((s) => s === 'reconnecting').length >= 1, 3000);
  });

  it('stop() unsubscribes, closes and never reconnects (resources released)', async () => {
    const w = await worker();
    const { obs } = observer(w);
    obs.start();
    await waitFor(() => obs.status === 'connected');
    obs.stop();
    await waitFor(() => w.clients.size === 0);
    expect(w.received.some((m) => m.type === 'monitor.unsubscribe')).toBe(true);
    await new Promise((r) => setTimeout(r, 300));
    expect(w.clients.size).toBe(0);
    expect(obs.status).toBe('stopped');
  });
});
