// Minimal stand-in of VisionWorker's observe-only protocol (apps/VisionWorker/live_monitor.py) on a real
// WebSocket server, for client tests. Tickets are opaque strings the test declares valid.
import { WebSocketServer, type WebSocket } from 'ws';
import type { AddressInfo } from 'node:net';

export class FakeVisionWorker {
  wss: WebSocketServer | null = null;
  port = 0;
  valid = new Set<string>();
  clients = new Set<WebSocket>();
  subscriptions: { ws: WebSocket; sessionId: string; ticket: string }[] = [];
  renewals: string[] = [];
  received: Record<string, unknown>[] = [];
  silent = false;

  async start(port = 0): Promise<void> {
    await new Promise<void>((resolve) => {
      this.wss = new WebSocketServer({ port, host: '127.0.0.1' }, () => resolve());
    });
    this.port = (this.wss!.address() as AddressInfo).port;
    this.wss!.on('connection', (ws) => {
      this.clients.add(ws);
      ws.on('close', () => {
        this.clients.delete(ws);
        this.subscriptions = this.subscriptions.filter((s) => s.ws !== ws);
      });
      ws.on('message', (raw) => {
        const msg = JSON.parse(String(raw));
        this.received.push(msg);
        if (this.silent) return;
        if (msg.type === 'monitor.subscribe') {
          if (!this.valid.has(msg.auth_token)) {
            ws.send(JSON.stringify({ type: 'error', code: 'unauthorized', message: 'bad ticket' }));
            ws.close();
            return;
          }
          this.subscriptions.push({ ws, sessionId: msg.session_id, ticket: msg.auth_token });
          ws.send(JSON.stringify({ type: 'monitor.subscribed', session_id: msg.session_id, expires_at: 0 }));
        } else if (msg.type === 'monitor.renew') {
          this.renewals.push(msg.auth_token);
          ws.send(JSON.stringify({ type: 'monitor.renewed' }));
        }
      });
    });
  }

  get url(): string {
    return `ws://127.0.0.1:${this.port}/ws`;
  }

  broadcast(payload: unknown): void {
    for (const s of this.subscriptions) s.ws.send(JSON.stringify(payload));
  }

  sendError(code: string): void {
    for (const s of this.subscriptions) s.ws.send(JSON.stringify({ type: 'error', code, message: code }));
  }

  /** Simulates a worker crash/restart: all sockets drop, server gone. */
  async stop(): Promise<void> {
    for (const ws of this.clients) ws.terminate();
    this.clients.clear();
    this.subscriptions = [];
    await new Promise<void>((resolve) => (this.wss ? this.wss.close(() => resolve()) : resolve()));
    this.wss = null;
  }
}

export async function waitFor(predicate: () => boolean, timeoutMs = 5000, stepMs = 20): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor: condition not met');
}
