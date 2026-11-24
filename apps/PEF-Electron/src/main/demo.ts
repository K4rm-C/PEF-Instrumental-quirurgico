// Demonstration mode (`--demo`): a scripted, clearly labelled SIMULATED source that goes through the real
// monitor pipeline (StationMonitor + VisionObserver + semaphore) without any backend. Every frame carries a
// "SIMULACIÓN" watermark and the UI shows "Fuente simulada (modo demostración)". It never talks to the network
// and is never presented as a physical camera.
import { ApiError } from './api';
import type { MonitorApi } from './monitor';
import type { WsFactory, WsLike } from './visionObserver';
import type { BackendSession, LiveTicket, StationOverview } from '../shared/types';

export const DEMO_STATION_ID = '00000000-0000-4000-8000-00000000d3e0';
const DEMO_SESSION_ID = '00000000-0000-4000-8000-0000000051d0';

const FAMILIES = [
  { family_id: 'demo-f1', family_code: 'pinza_kelly', family_name: 'Pinza Kelly', expected_quantity: 4 },
  { family_id: 'demo-f2', family_code: 'tijera_mayo', family_name: 'Tijera Mayo', expected_quantity: 3 },
  { family_id: 'demo-f3', family_code: 'portaagujas', family_name: 'Portaagujas', expected_quantity: 4 },
  { family_id: 'demo-f4', family_code: 'separador', family_name: 'Separador Farabeuf', expected_quantity: 4 },
];

interface Step {
  at: number; // seconds into the loop
  status: string | null; // null = no session
  phase: string | null;
  counts: Record<string, number> | null;
  official?: BackendSession['official']['result'];
}

const FULL = { pinza_kelly: 4, tijera_mayo: 3, portaagujas: 4, separador: 4 };
const SCRIPT: Step[] = [
  { at: 0, status: null, phase: null, counts: null },
  { at: 6, status: 'in_progress', phase: 'start', counts: FULL },
  { at: 20, status: 'in_progress', phase: 'demo_phase_1', counts: { ...FULL, pinza_kelly: 2, tijera_mayo: 2 } },
  { at: 34, status: 'in_progress', phase: 'demo_phase_2', counts: FULL },
  { at: 44, status: 'in_progress', phase: 'final_count', counts: { ...FULL, portaagujas: 2 } },
  { at: 58, status: 'in_progress', phase: 'final_count', counts: FULL },
  { at: 68, status: 'awaiting_spd_review', phase: 'final_count', counts: null, official: 'validated_match' },
  { at: 80, status: 'closed', phase: 'final_count', counts: null, official: 'closed_clean' },
  { at: 92, status: null, phase: null, counts: null },
];
const LOOP_S = 100;

export class DemoWorld {
  private readonly startedAt: number;
  constructor(private readonly now: () => number = Date.now) {
    this.startedAt = now();
  }

  step(): Step {
    const t = ((this.now() - this.startedAt) / 1000) % LOOP_S;
    let current = SCRIPT[0];
    for (const s of SCRIPT) if (t >= s.at) current = s;
    return current;
  }

  /** Small deterministic jitter so the stabiliser has something to do (one odd frame every ~7). */
  counts(frame: number): Record<string, number> | null {
    const s = this.step();
    if (!s.counts) return null;
    if (frame % 7 === 3) return { ...s.counts, separador: Math.max(0, s.counts.separador - 1) };
    return s.counts;
  }

  session(): BackendSession | null {
    const s = this.step();
    if (!s.status) return null;
    const result = s.official ?? 'pending';
    return {
      session_id: DEMO_SESSION_ID,
      session_ref: 'DEMO0001',
      station_id: DEMO_STATION_ID,
      status_code: s.status,
      status_name: s.status,
      phase_code: s.phase,
      phase_name: s.phase,
      phase_changed_at: null,
      capture_mode: 'vision',
      procedure_name: 'Procedimiento de demostración',
      procedure_code: 'demo',
      patient_name: 'Paciente de demostración',
      doctor_name: 'Dra. Ejemplo',
      started_at: new Date(this.startedAt).toISOString(),
      ended_at: null,
      updated_at: null,
      camera: { id: 'demo-cam', name: 'Fuente simulada', device_type: 'simulated', simulated: true },
      reference: { total: 15, version: 'demo-v1', families: FAMILIES },
      official: {
        result,
        validated: result !== 'pending',
        validated_at: null,
        close_path: result !== 'pending' ? 'manual_count' : null,
        open_discrepancies: 0,
        discrepancies_total: 0,
      },
      live_available: s.status === 'in_progress',
    };
  }
}

export class DemoApi implements MonitorApi {
  identity = { email: 'demostracion@pef.local' };
  constructor(private readonly world: DemoWorld) {}
  hasCredentials() {
    return true;
  }
  async stationOverview(): Promise<StationOverview> {
    const session = this.world.session();
    const active = session && session.status_code === 'in_progress' ? session : null;
    return {
      server_time: new Date().toISOString(),
      station: { id: DEMO_STATION_ID, name: 'Estación de demostración', room_code: 'DEMO', room_name: 'Quirófano demo' },
      camera_registry: {
        registry_available: true,
        cameras: [{ id: 'demo-cam', name: 'Fuente simulada', device_type: 'simulated', simulated: true, active: true }],
        last_report: null,
      },
      state: active ? 'active' : 'idle',
      session: active,
      candidates: [],
      live_monitor_enabled: true,
    };
  }
  async sessionMonitor(): Promise<BackendSession> {
    const s = this.world.session();
    if (!s) throw new ApiError(404, 'not_found', 'Session not found');
    return s;
  }
  async liveTicket(): Promise<LiveTicket> {
    return { session_id: DEMO_SESSION_ID, ticket: 'demo-ticket', ws_url: 'demo://vision', expires_at: Date.now() / 1000 + 60, ttl_s: 60, scope: 'observe' };
  }
}

const SHAPE_COLORS: Record<string, string> = {
  pinza_kelly: '#2bb3a0',
  tijera_mayo: '#5b8def',
  portaagujas: '#e0a030',
  separador: '#c46bd6',
};

export function demoFrameSvg(counts: Record<string, number>, frame: number): string {
  const parts: string[] = [];
  let x = 60;
  let y = 70;
  for (const fam of FAMILIES) {
    const n = counts[fam.family_code] ?? 0;
    for (let i = 0; i < n; i++) {
      const color = SHAPE_COLORS[fam.family_code];
      parts.push(
        `<rect x="${x}" y="${y}" width="120" height="34" rx="10" fill="none" stroke="${color}" stroke-width="4"/>` +
          `<rect x="${x + 10}" y="${y + 12}" width="100" height="10" rx="5" fill="#c9d3dc"/>` +
          `<text x="${x}" y="${y - 6}" font-size="13" fill="${color}" font-family="Segoe UI, Arial">${fam.family_name} 0.9${(frame + i) % 10}</text>`,
      );
      x += 150;
      if (x > 1040) {
        x = 60;
        y += 80;
      }
    }
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">` +
    `<rect width="1280" height="720" fill="#1b2430"/>` +
    `<rect x="30" y="30" width="1220" height="660" rx="18" fill="#26323f" stroke="#3b4a5a" stroke-width="3"/>` +
    parts.join('') +
    `<text x="640" y="640" text-anchor="middle" font-size="44" font-weight="700" fill="#ffffff" fill-opacity="0.18" font-family="Segoe UI, Arial">SIMULACIÓN · NO ES UNA CÁMARA FÍSICA</text>` +
    `</svg>`
  );
}

class DemoSocket implements WsLike {
  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private frame = 0;

  constructor(private readonly world: DemoWorld) {
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.({});
    }, 50);
  }

  private push(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  send(data: string): void {
    const msg = JSON.parse(data) as { type: string };
    if (msg.type === 'monitor.subscribe') {
      this.push({ type: 'monitor.subscribed', session_id: DEMO_SESSION_ID, expires_at: Date.now() / 1000 + 60 });
      this.timer = setInterval(() => this.tick(), 500);
    } else if (msg.type === 'monitor.renew') {
      this.push({ type: 'monitor.renewed', session_id: DEMO_SESSION_ID });
    } else if (msg.type === 'monitor.unsubscribe') {
      this.close();
    }
  }

  private tick() {
    const counts = this.world.counts(++this.frame);
    if (!counts) return;
    const detections = FAMILIES.flatMap((f) =>
      Array.from({ length: counts[f.family_code] ?? 0 }, () => ({
        family_code: f.family_code,
        family_name: f.family_name,
        confidence: 0.9,
        high_confidence: true,
        held: false,
        bbox: [0, 0, 10, 10],
      })),
    );
    this.push({
      type: 'monitor.state',
      session_id: DEMO_SESSION_ID,
      sent_at: new Date().toISOString(),
      worker: { status: 'processing', capture_source: 'live', message: 'demo', run_id: 'demo', frames_processed: this.frame, last_frame_at: new Date().toISOString(), frame_age_s: 0.05 },
      frame: { jpeg_b64: Buffer.from(demoFrameSvg(counts, this.frame)).toString('base64'), frames_processed: this.frame, run_id: 'demo', mime: 'image/svg+xml' },
      counts_by_family: FAMILIES.map((f) => ({ family_code: f.family_code, detected_quantity: counts[f.family_code] ?? 0 })),
      detections,
    });
  }

  close(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({});
  }
}

export function demoWsFactory(world: DemoWorld): WsFactory {
  return () => new DemoSocket(world);
}
