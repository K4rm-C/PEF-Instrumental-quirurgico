// Observe-only client of VisionWorker /ws (ELECTRON-01). Runs in the MAIN process: the ticket never reaches
// the renderer. Protocol: apps/VisionWorker/live_monitor.py.
//
// - subscribe with a short-lived ticket issued by SessionService for THIS session;
// - renew every ttl/3 (each renewal re-checks the JWT, role, institution, station and session server side);
// - reconnect with exponential backoff (1 → 15 s) after an unexpected loss (worker restart, network);
// - a watchdog treats silence longer than `watchdogMs` as a lost link;
// - messages of another session are dropped;
// - never reconnects after an authorisation refusal or when stopped.
import { ApiError } from './api';
import type { LiveTicket, MonitorStateMessage } from '../shared/types';

export interface WsLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type WsFactory = (url: string) => WsLike;

export type ObserverStatus = 'connecting' | 'connected' | 'reconnecting' | 'unavailable' | 'unauthorized' | 'stopped';

export interface ObserverOptions {
  sessionId: string;
  getTicket: () => Promise<LiveTicket>;
  resolveUrl: (ticket: LiveTicket) => string;
  wsFactory: WsFactory;
  onState: (msg: MonitorStateMessage) => void;
  onStatus: (status: ObserverStatus, detail?: string) => void;
  log?: (message: string) => void;
  backoffMs?: number[];
  watchdogMs?: number;
  unavailableRetryMs?: number;
}

const OPEN = 1;

export class VisionObserver {
  private ws: WsLike | null = null;
  private stopped = false;
  private attempt = 0;
  private authFailures = 0;
  private renewTimer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private subscribed = false;
  status: ObserverStatus = 'connecting';

  private readonly backoff: number[];
  private readonly watchdogMs: number;
  private readonly unavailableRetryMs: number;

  constructor(private readonly opts: ObserverOptions) {
    this.backoff = opts.backoffMs ?? [1000, 2000, 4000, 8000, 15000];
    this.watchdogMs = opts.watchdogMs ?? 7000;
    this.unavailableRetryMs = opts.unavailableRetryMs ?? 20000;
  }

  get sessionId(): string {
    return this.opts.sessionId;
  }

  start(): void {
    this.stopped = false;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.onerror = null;
      try {
        if (ws.readyState === OPEN) ws.send(JSON.stringify({ type: 'monitor.unsubscribe' }));
        ws.close(1000, 'monitor stopped');
      } catch {
        /* already closed */
      }
    }
    this.setStatus('stopped');
  }

  private setStatus(status: ObserverStatus, detail?: string): void {
    this.status = status;
    this.opts.onStatus(status, detail);
  }

  private clearTimers(): void {
    if (this.renewTimer) clearTimeout(this.renewTimer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.watchdog) clearInterval(this.watchdog);
    this.renewTimer = this.retryTimer = this.watchdog = null;
  }

  private terminal(status: 'unavailable' | 'unauthorized', detail: string): void {
    this.opts.log?.(`vision observer ${status}: ${detail}`);
    this.stop();
    this.setStatus(status, detail);
  }

  /** ApiError from the ticket endpoint → what to do. */
  private handleTicketError(err: unknown): void {
    if (err instanceof ApiError) {
      if (err.status === 401 || err.status === 403 || err.status === 404) {
        return this.terminal('unauthorized', err.code);
      }
      if (err.status === 409) return this.terminal('unavailable', err.code); // session left in_progress
      if (err.status === 503 && err.code === 'monitor_unavailable') {
        this.setStatus('unavailable', err.code);
        return this.scheduleRetry(this.unavailableRetryMs);
      }
    }
    this.scheduleRetry();
  }

  private scheduleRetry(delay?: number): void {
    if (this.stopped) return;
    this.clearTimers();
    this.dropSocket();
    const wait = delay ?? this.backoff[Math.min(this.attempt, this.backoff.length - 1)];
    this.attempt += 1;
    if (this.status !== 'unavailable') this.setStatus('reconnecting');
    this.retryTimer = setTimeout(() => void this.connect(), wait);
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.subscribed = false;
    this.dropSocket(); // never keep two sockets of the same observer
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting');
    let ticket: LiveTicket;
    try {
      ticket = await this.opts.getTicket();
    } catch (err) {
      return this.handleTicketError(err);
    }
    if (this.stopped) return;
    if (ticket.session_id !== this.opts.sessionId) return this.terminal('unauthorized', 'ticket_session_mismatch');
    let ws: WsLike;
    try {
      ws = this.opts.wsFactory(this.opts.resolveUrl(ticket));
    } catch (err) {
      this.opts.log?.(`vision observer: cannot open socket ${(err as Error).message}`);
      return this.scheduleRetry();
    }
    this.ws = ws;
    this.lastMessageAt = Date.now();
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'monitor.subscribe', session_id: this.opts.sessionId, auth_token: ticket.ticket }));
    };
    ws.onmessage = (ev) => this.onMessage(ws, ev.data, ticket);
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.stopped) this.scheduleRetry();
    };
    this.watchdog = setInterval(() => {
      if (this.ws === ws && Date.now() - this.lastMessageAt > this.watchdogMs) {
        this.opts.log?.('vision observer: watchdog timeout');
        try {
          ws.close(4000, 'watchdog');
        } catch {
          /* ignore */
        }
        // Some implementations do not emit close for a half-open socket: force the retry.
        if (this.ws === ws) {
          this.ws = null;
          this.scheduleRetry();
        }
      }
    }, Math.max(250, Math.floor(this.watchdogMs / 4)));
  }

  private scheduleRenew(ttlS: number): void {
    if (this.renewTimer) clearTimeout(this.renewTimer);
    const delay = Math.max(1000, Math.floor((ttlS * 1000) / 3));
    this.renewTimer = setTimeout(async () => {
      const ws = this.ws;
      if (!ws || this.stopped) return;
      try {
        const next = await this.opts.getTicket();
        if (this.stopped || this.ws !== ws) return;
        ws.send(JSON.stringify({ type: 'monitor.renew', auth_token: next.ticket }));
        this.scheduleRenew(next.ttl_s);
      } catch (err) {
        if (this.stopped || this.ws !== ws) return;
        if (!(err instanceof ApiError) || err.isNetwork) {
          // SessionService briefly unreachable: keep the live socket (the current ticket is still valid) and
          // retry the renewal soon. If it really expires, the worker says so and we reconnect then.
          this.opts.log?.('vision observer: ticket renewal deferred (backend unreachable)');
          this.scheduleRenew(Math.min(ttlS, 9));
          return;
        }
        this.handleTicketError(err);
      }
    }, delay);
  }

  private onMessage(ws: WsLike, data: unknown, ticket: LiveTicket): void {
    if (this.ws !== ws) return;
    this.lastMessageAt = Date.now();
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(String(data));
    } catch {
      return;
    }
    if (!msg || typeof msg !== 'object') return;
    switch (msg.type) {
      case 'monitor.subscribed':
        if (msg.session_id !== this.opts.sessionId) return this.terminal('unauthorized', 'foreign_session');
        this.subscribed = true;
        this.attempt = 0;
        this.authFailures = 0;
        this.setStatus('connected');
        this.scheduleRenew(ticket.ttl_s);
        return;
      case 'monitor.renewed':
        return;
      case 'monitor.state':
        if (!this.subscribed || msg.session_id !== this.opts.sessionId) {
          this.opts.log?.('vision observer: dropped message of another session');
          return;
        }
        this.opts.onState(msg as unknown as MonitorStateMessage);
        return;
      case 'error': {
        const code = String(msg.code ?? 'error');
        if (code === 'unauthorized' || code === 'session_expired') {
          // A fresh ticket may fix a race (expiry during reconnection); repeated refusals are final.
          this.authFailures += 1;
          if (this.authFailures > 3) return this.terminal('unauthorized', code);
          this.closeAndRetry(ws);
          return;
        }
        if (code === 'monitor_disabled') return this.terminal('unavailable', code);
        if (code === 'busy') {
          this.closeAndRetry(ws);
          return;
        }
        this.opts.log?.(`vision observer: worker error ${code}`);
        return;
      }
      default:
        return;
    }
  }

  private dropSocket(): void {
    const old = this.ws;
    this.ws = null;
    if (!old) return;
    old.onclose = null;
    old.onmessage = null;
    try {
      old.close(1000, 'replaced');
    } catch {
      /* ignore */
    }
  }

  private closeAndRetry(ws: WsLike): void {
    this.ws = null;
    ws.onclose = null;
    try {
      ws.close(1000, 'retry');
    } catch {
      /* ignore */
    }
    this.scheduleRetry();
  }
}
