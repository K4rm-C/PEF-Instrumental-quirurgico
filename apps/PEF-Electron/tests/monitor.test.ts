// StationMonitor end to end in-process: scripted SessionService (fake API) + real WebSocket worker stand-in.
import { afterEach, describe, expect, it } from 'vitest';
import { ApiError } from '../src/main/api';
import { StationMonitor, type MonitorApi } from '../src/main/monitor';
import type { WsLike } from '../src/main/visionObserver';
import { DEFAULT_SEMAPHORE_CONFIG } from '../src/shared/semaphore';
import type { BackendSession, LiveTicket, MonitorSnapshot, StationOverview } from '../src/shared/types';
import { FULL, OTHER_STATION, SID, SID2, STATION, overview, session, stateMsg } from './fixtures';
import { FakeVisionWorker, waitFor } from './wsServer';

class FakeSessionService implements MonitorApi {
  identity = { email: 'op@instrumed.com' };
  credentials = true;
  overviewResult: StationOverview | Error = overview('idle');
  sessions = new Map<string, BackendSession>();
  ticketsIssued = 0;
  ticketError: Error | null = null;
  constructor(private readonly worker: FakeVisionWorker) {}
  hasCredentials() {
    return this.credentials;
  }
  async stationOverview(stationId: string) {
    if (stationId !== STATION) throw new ApiError(404, 'not_found', 'Station not found');
    if (this.overviewResult instanceof Error) throw this.overviewResult;
    return this.overviewResult;
  }
  async sessionMonitor(_station: string, sessionId: string) {
    const s = this.sessions.get(sessionId);
    if (!s) throw new ApiError(404, 'not_found', 'Session not found');
    return s;
  }
  async liveTicket(_station: string, sessionId: string): Promise<LiveTicket> {
    if (this.ticketError) throw this.ticketError;
    const t = `ticket-${++this.ticketsIssued}`;
    this.worker.valid.add(t);
    return { session_id: sessionId, ticket: t, ws_url: this.worker.url, expires_at: 0, ttl_s: 60, scope: 'observe' };
  }
  /** Operator starts / updates the session in the portal. */
  active(s: BackendSession) {
    this.sessions.set(s.session_id, s);
    this.overviewResult = overview('active', s);
  }
  /** Session leaves in_progress (validation submitted, closed...). */
  leave(s: BackendSession) {
    this.sessions.set(s.session_id, s);
    this.overviewResult = overview('idle');
  }
}

const cleanups: (() => Promise<void> | void)[] = [];
afterEach(async () => {
  for (const c of cleanups.splice(0)) await c();
});

async function setup(opts: { stationId?: string } = {}) {
  const worker = new FakeVisionWorker();
  await worker.start();
  const api = new FakeSessionService(worker);
  const snaps: MonitorSnapshot[] = [];
  let activations = 0;
  let idles = 0;
  const logs: string[] = [];
  const make = () =>
    new StationMonitor({
      api,
      stationId: () => opts.stationId ?? STATION,
      configured: () => true,
      resolveWsUrl: (hint) => hint ?? worker.url,
      wsFactory: (url) => new WebSocket(url) as unknown as WsLike,
      emit: (s) => snaps.push(s),
      activate: () => (activations += 1),
      idle: () => (idles += 1),
      log: { info: (m) => logs.push(m), warn: (m) => logs.push(m) },
      autostart: () => false,
      options: {
        pollActiveMs: 40,
        pollIdleMs: 40,
        maxBackoffMs: 200,
        reviewHoldMs: 400,
        endedHoldMs: 400,
        tickMs: 40,
        semaphore: { ...DEFAULT_SEMAPHORE_CONFIG, minReadings: 2, confirmReadings: 2, minHoldMs: 0, staleMs: 1500 },
        observerBackoffMs: [50, 100],
        observerWatchdogMs: 5000,
      },
    });
  const monitor = make();
  cleanups.push(async () => {
    monitor.stop();
    await worker.stop();
  });
  const last = () => snaps[snaps.length - 1];
  return { worker, api, monitor, make, snaps, last, logs, activations: () => activations, idles: () => idles };
}

async function feed(worker: FakeVisionWorker, counts: Record<string, number>, from: number, n = 3, sessionId = SID) {
  for (let i = 0; i < n; i++) {
    worker.broadcast({ ...stateMsg(counts, from + i), session_id: sessionId });
    await new Promise((r) => setTimeout(r, 30));
  }
}

describe('StationMonitor', () => {
  it('no active session: waiting screen, gray, no observer', async () => {
    const t = await setup();
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'waiting');
    expect(t.last().semaphore.color).toBe('gray');
    expect(t.last().station?.name).toBe('Estación 1');
    expect(t.worker.clients.size).toBe(0);
    expect(t.activations()).toBe(0);
  });

  it('session started from the portal: monitor activates once and follows it live', async () => {
    const t = await setup();
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'waiting');
    t.api.active(session({ phase_code: 'start' }));
    await waitFor(() => t.last()?.appState === 'monitoring' && t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    expect(t.last().phase).toBe('initial');
    expect(t.worker.subscriptions[0].sessionId).toBe(SID);
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().semaphore.color === 'green');
    expect(t.last().semaphore).toMatchObject({ kind: 'provisional', observed: 7, reference: 7 });
    expect(t.last().frame?.dataUrl.startsWith('data:image/jpeg;base64,')).toBe(true);
    // Many polls later: still one window activation and one subscription (no duplicates).
    await new Promise((r) => setTimeout(r, 300));
    expect(t.activations()).toBe(1);
    expect(t.worker.subscriptions.length).toBe(1);
  });

  it('yellow during the procedure, back to green, red in the official final verification', async () => {
    const t = await setup();
    t.api.active(session({ phase_code: 'demo_phase_1' }));
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, { kelly: 2, mayo: 3 }, 1);
    await waitFor(() => t.last().semaphore.color === 'yellow');
    expect(t.last().semaphore.missing).toBe(2);
    expect(t.last().families.find((f) => f.familyCode === 'kelly')?.status).toBe('missing');
    await feed(t.worker, FULL, 10);
    await waitFor(() => t.last().semaphore.color === 'green');
    // The Operator enters the final verification in the portal (phase change comes from the backend).
    t.api.active(session({ phase_code: 'final_count' }));
    await waitFor(() => t.last().phase === 'final');
    await feed(t.worker, { kelly: 3, mayo: 3 }, 20, 4);
    await waitFor(() => t.last().semaphore.color === 'red');
    expect(t.last().semaphore.message).toBe('Instrumental faltante durante la verificación final. Requiere conciliación.');
    // A correct recapture is reflected.
    await feed(t.worker, FULL, 30, 4);
    await waitFor(() => t.last().semaphore.color === 'green');
    expect(t.last().semaphore.kind).toBe('provisional');
  });

  it('a lower camera count alone never moves the phase to final verification', async () => {
    const t = await setup();
    t.api.active(session({ phase_code: 'demo_phase_2' }));
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, { kelly: 0, mayo: 0 }, 1, 5);
    await waitFor(() => t.last().semaphore.color === 'yellow');
    expect(t.last().phase).toBe('procedure');
  });

  it('session ends: observer stopped, frame released, official outcome shown, then back to waiting', async () => {
    const t = await setup();
    const s = session({ phase_code: 'final_count' });
    t.api.active(s);
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().frame !== null);
    t.api.leave({ ...s, status_code: 'awaiting_spd_review', official: { ...s.official, result: 'validated_match', validated: true } });
    await waitFor(() => t.last().appState === 'review');
    expect(t.last().semaphore).toMatchObject({ color: 'green', kind: 'confirmed' });
    expect(t.last().frame).toBeNull();
    await waitFor(() => t.worker.clients.size === 0);
    // SPD closes it in the portal: ended.
    t.api.sessions.set(SID, { ...s, status_code: 'closed', official: { ...s.official, result: 'closed_clean', validated: true } });
    await waitFor(() => t.last().appState === 'ended');
    await waitFor(() => t.last().appState === 'waiting', 3000);
    expect(t.idles()).toBe(1);
    expect(t.last().session).toBeNull();
  });

  it('unresolved outcome is never shown as a successful close', async () => {
    const t = await setup();
    const s = session();
    t.api.active(s);
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'monitoring');
    t.api.leave({ ...s, status_code: 'correction_required', official: { ...s.official, result: 'discrepancy', validated: true, open_discrepancies: 2 } });
    await waitFor(() => t.last().appState === 'review');
    expect(t.last().semaphore.color).toBe('red');
    expect(t.last().semaphore.kind).not.toBe('confirmed');
  });

  it('switches to a new session without duplicates and never mixes their data', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    t.api.active(session({ session_id: SID2, session_ref: 'SECOND' }));
    await waitFor(() => t.last().session?.id === SID2 && t.worker.subscriptions.some((x) => x.sessionId === SID2));
    expect(t.last().frame).toBeNull();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    expect(t.worker.subscriptions[0].sessionId).toBe(SID2);
    // A late message of the old session is ignored.
    t.worker.broadcast(stateMsg({ kelly: 1 }, 99));
    await new Promise((r) => setTimeout(r, 150));
    expect(t.last().detections.length).toBe(0);
    expect(t.activations()).toBe(2);
  });

  it('rejects a session that belongs to another station', async () => {
    const t = await setup();
    t.api.active(session({ station_id: OTHER_STATION }));
    t.monitor.start();
    await new Promise((r) => setTimeout(r, 300));
    expect(t.last().session).toBeNull();
    expect(t.worker.clients.size).toBe(0);
    expect(t.activations()).toBe(0);
  });

  it('station of another institution (404) is a configuration error, not a session', async () => {
    const t = await setup({ stationId: OTHER_STATION });
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'error');
    expect(t.last().notice).toMatch(/no pertenece a su institución/);
  });

  it('ambiguous station: no session is chosen', async () => {
    const t = await setup();
    t.api.overviewResult = overview('ambiguous', null, {
      candidates: [{ session_id: SID, session_ref: 'A', started_at: null }, { session_id: SID2, session_ref: 'B', started_at: null }],
    });
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'ambiguous');
    expect(t.last().session).toBeNull();
    expect(t.last().candidates.length).toBe(2);
    expect(t.worker.clients.size).toBe(0);
  });

  it('revocation (401): stops the video immediately and asks for login', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    t.api.overviewResult = new ApiError(401, 'unauthorized', 'Session is no longer valid');
    await waitFor(() => t.last().appState === 'login_required');
    expect(t.last().semaphore.color).toBe('gray');
    await waitFor(() => t.worker.clients.size === 0);
  });

  it('SessionService restart: gray while unreachable, recovers the same session', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().semaphore.color === 'green');
    const saved = t.api.overviewResult;
    t.api.overviewResult = new ApiError(0, 'network', 'ECONNREFUSED');
    await waitFor(() => t.last().connection.backend === 'reconnecting');
    expect(t.last().semaphore).toMatchObject({ color: 'gray', reason: 'backend_unreachable' });
    t.api.overviewResult = saved;
    await waitFor(() => t.last().connection.backend === 'ok');
    expect(t.last().session?.id).toBe(SID);
    await feed(t.worker, FULL, 10);
    await waitFor(() => t.last().semaphore.color === 'green');
  });

  it('VisionWorker restart: gray, then the observer comes back by itself', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().semaphore.color === 'green');
    const port = t.worker.port;
    await t.worker.stop();
    await waitFor(() => t.last().semaphore.color === 'gray');
    await t.worker.start(port);
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected', 10000);
    await waitFor(() => t.last().vision.link === 'connected');
    // After a restart the worker forgot the session until the portal re-binds it.
    t.worker.broadcast({ ...stateMsg({}, 0), worker: { ...stateMsg({}, 0).worker, status: 'unbound' }, frame: null });
    await waitFor(() => t.last().semaphore.reason === 'worker_unbound');
    await feed(t.worker, FULL, 1, 3);
    await waitFor(() => t.last().semaphore.color === 'green');
  });

  it('stale data turns gray instead of keeping an old count', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().semaphore.color === 'green');
    await waitFor(() => t.last().semaphore.reason === 'stale', 4000); // no new inference for > staleMs
  });

  it('out-of-order frames are ignored', async () => {
    const t = await setup();
    t.api.active(session());
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 5);
    await waitFor(() => t.last().semaphore.color === 'green');
    await feed(t.worker, { kelly: 0, mayo: 0 }, 1, 3); // older frames of the same run
    await new Promise((r) => setTimeout(r, 200));
    expect(t.last().semaphore.color).toBe('green');
    expect(t.last().frame?.key).toBe('run1:7');
  });

  it('reference change (instruments added by SPD) resets the window and uses the new reference', async () => {
    const t = await setup();
    const s = session();
    t.api.active(s);
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    await feed(t.worker, FULL, 1);
    await waitFor(() => t.last().semaphore.color === 'green');
    const families = [...s.reference.families, { family_id: 'f-c', family_code: 'gauze_clamp', family_name: 'Clamp', expected_quantity: 1 }];
    t.api.active({ ...s, reference: { total: 8, version: 'v2', families } });
    await waitFor(() => t.last().reference?.version === 'v2');
    await feed(t.worker, FULL, 10);
    await waitFor(() => t.last().semaphore.color === 'yellow');
    expect(t.last().semaphore).toMatchObject({ observed: 7, reference: 8, missing: 1 });
  });

  it('recovery after an Electron restart: a new monitor picks up the same active session', async () => {
    const t = await setup();
    t.api.active(session({ phase_code: 'final_count' }));
    t.monitor.start();
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
    t.monitor.stop();
    await waitFor(() => t.worker.clients.size === 0);
    const again = t.make();
    cleanups.push(() => again.stop());
    again.start();
    await waitFor(() => t.last().appState === 'monitoring' && t.last().phase === 'final');
    await waitFor(() => t.worker.subscriptions.length === 1 && t.last()?.vision.link === 'connected');
  });

  it('manual-capture sessions are shown without video and never green', async () => {
    const t = await setup();
    t.api.active(session({ capture_mode: 'manual_no_privacy', live_available: false }));
    t.monitor.start();
    await waitFor(() => t.last()?.appState === 'monitoring');
    expect(t.last().vision.link).toBe('unavailable');
    expect(t.last().semaphore.reason).toBe('manual_mode');
    expect(t.worker.clients.size).toBe(0);
  });
});
