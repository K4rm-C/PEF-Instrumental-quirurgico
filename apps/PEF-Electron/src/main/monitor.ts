// Station monitor orchestrator (ELECTRON-01 phases 2, 3, 4, 5 and 7). Electron-free and fully injectable.
//
// Synchronisation with the portal is pull-based on the EXISTING source of truth: SessionService answers which
// session is in_progress at this station for this Operator (no event bus exists for session/phase changes; the
// only WebSocket in the system is VisionWorker's live channel). Polling every 2 s while monitoring / 4 s while
// waiting keeps the gateway far below its rate limit and survives restarts of any component without state of
// its own: after an Electron or service restart, the next successful poll restores the exact same view.
import { ApiError } from './api';
import { VisionObserver, type ObserverStatus, type WsFactory } from './visionObserver';
import { phaseLabel, uiPhaseFor } from '../shared/phases';
import {
  DEFAULT_SEMAPHORE_CONFIG,
  UiStabilizer,
  evaluateRaw,
  familyRows,
  type SemaphoreConfig,
} from '../shared/semaphore';
import type {
  AppState,
  BackendSession,
  LiveTicket,
  MonitorSnapshot,
  MonitorStateMessage,
  Reading,
  SemaphoreView,
  SourceKind,
  StationOverview,
  UiPhase,
  WorkerDetection,
} from '../shared/types';

export interface MonitorApi {
  hasCredentials(): boolean;
  identity: { email: string; locale?: 'en' | 'es-MX' } | null;
  stationOverview(stationId: string): Promise<StationOverview>;
  sessionMonitor(stationId: string, sessionId: string): Promise<BackendSession>;
  liveTicket(stationId: string, sessionId: string): Promise<LiveTicket>;
}

export interface MonitorOptions {
  pollActiveMs: number;
  pollIdleMs: number;
  maxBackoffMs: number;
  reviewHoldMs: number;
  endedHoldMs: number;
  tickMs: number;
  semaphore: SemaphoreConfig;
  observerBackoffMs?: number[];
  observerWatchdogMs?: number;
}

export const DEFAULT_MONITOR_OPTIONS: MonitorOptions = {
  pollActiveMs: 2000,
  pollIdleMs: 4000,
  maxBackoffMs: 15000,
  reviewHoldMs: 180000,
  endedHoldMs: 60000,
  tickMs: 1000,
  semaphore: DEFAULT_SEMAPHORE_CONFIG,
};

export interface MonitorDeps {
  api: MonitorApi;
  stationId: () => string;
  configured: () => boolean;
  resolveWsUrl: (serverHint: string | null) => string;
  wsFactory: WsFactory;
  emit: (snapshot: MonitorSnapshot) => void;
  activate: () => void;
  idle: () => void;
  log: { info(m: string, e?: unknown): void; warn(m: string, e?: unknown): void };
  autostart: () => boolean;
  demo?: boolean;
  now?: () => number;
  options?: Partial<MonitorOptions>;
}

const ALLOWED_FRAME_MIME = new Set(['image/jpeg', 'image/svg+xml']);
const MAX_READINGS = 24;

export class StationMonitor {
  private readonly opts: MonitorOptions;
  private readonly now: () => number;
  private appState: AppState = 'connecting';
  private backend: MonitorSnapshot['connection']['backend'] = 'unknown';
  private lastSyncAt: string | null = null;
  private notice: string | null = null;
  private overview: StationOverview | null = null;
  private followed: BackendSession | null = null;
  private leftActiveAt: number | null = null;
  private lastStatus: string | null = null;
  private candidates: MonitorSnapshot['candidates'] = [];
  private failures = 0;

  private observer: VisionObserver | null = null;
  private link: MonitorSnapshot['vision']['link'] = 'idle';
  private linkDetail: string | null = null;
  private workerStatus: string | null = null;
  private captureSource: string | null = null;
  private capturePhase: string | null = null;
  private recordingRunId: string | null = null;
  private observedRunId: string | null = null;
  private frame: MonitorSnapshot['frame'] = null;
  private frameAgeS: number | null = null;
  private lastUpdateAt: string | null = null;
  private detections: WorkerDetection[] = [];
  private readings: Reading[] = [];
  private stabilizer: UiStabilizer;
  private localVideoInputs: number | null = null;

  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private running = false;
  private polling = false;
  private lastEmit = 0;
  private emitPending: ReturnType<typeof setTimeout> | null = null;
  private lastView: SemaphoreView | null = null;

  constructor(private readonly deps: MonitorDeps) {
    this.opts = { ...DEFAULT_MONITOR_OPTIONS, ...(deps.options ?? {}) };
    this.now = deps.now ?? Date.now;
    this.stabilizer = new UiStabilizer(this.opts.semaphore);
  }

  // --- lifecycle ------------------------------------------------------------------------------------------

  start(): void {
    if (this.running) return;
    this.running = true;
    this.tickTimer = setInterval(() => this.emitNow(), this.opts.tickMs);
    void this.poll();
  }

  stop(): void {
    this.running = false;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.tickTimer) clearInterval(this.tickTimer);
    if (this.emitPending) clearTimeout(this.emitPending);
    this.pollTimer = this.tickTimer = this.emitPending = null;
    this.stopObserver();
  }

  /** Poll now (config saved, login, manual retry). */
  refreshNow(): void {
    if (!this.running) return;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    void this.poll();
  }

  /** Credentials removed (logout / revocation from the UI). */
  signedOut(message: string | null = null): void {
    this.stopObserver();
    this.releaseLive();
    this.followed = null;
    this.appState = 'login_required';
    this.backend = 'unauthorized';
    this.notice = message;
    this.emitNow();
  }

  setLocalVideoInputs(n: number): void {
    this.localVideoInputs = n;
    this.scheduleEmit();
  }

  get observedSessionId(): string | null {
    return this.observer?.sessionId ?? null;
  }

  // --- polling --------------------------------------------------------------------------------------------

  private schedule(ms: number): void {
    if (!this.running) return;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => void this.poll(), ms);
  }

  private nextDelay(): number {
    if (this.failures > 0) return Math.min(this.opts.pollIdleMs * 2 ** (this.failures - 1), this.opts.maxBackoffMs);
    return this.followed && this.appState === 'monitoring' ? this.opts.pollActiveMs : this.opts.pollIdleMs;
  }

  async poll(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    try {
      if (!this.deps.configured()) {
        this.appState = 'setup';
        this.stopObserver();
        return;
      }
      if (!this.deps.api.hasCredentials()) {
        this.appState = 'login_required';
        this.stopObserver();
        this.releaseLive();
        this.followed = null;
        return;
      }
      const overview = await this.deps.api.stationOverview(this.deps.stationId());
      if (overview.station.id.toLowerCase() !== this.deps.stationId().toLowerCase()) {
        throw new ApiError(404, 'station_mismatch', 'Respuesta de otra estación');
      }
      this.overview = overview;
      this.backend = 'ok';
      this.failures = 0;
      this.lastSyncAt = new Date(this.now()).toISOString();
      if (this.appState === 'error' || this.appState === 'login_required' || this.appState === 'connecting') this.notice = null;
      if (overview.state === 'active' && overview.session) await this.onActive(overview.session);
      else if (overview.state === 'ambiguous') this.onAmbiguous(overview);
      else await this.onIdle();
    } catch (err) {
      this.onError(err);
    } finally {
      this.polling = false;
      this.emitNow();
      this.schedule(this.nextDelay());
    }
  }

  private async onActive(session: BackendSession): Promise<void> {
    this.candidates = [];
    if (session.station_id.toLowerCase() !== this.deps.stationId().toLowerCase()) {
      this.deps.log.warn('ignored session of another station');
      return;
    }
    const isNew = !this.followed || this.followed.session_id !== session.session_id;
    if (isNew) {
      if (this.followed) this.deps.log.info(`session switch ${this.followed.session_ref} -> ${session.session_ref}`);
      else this.deps.log.info(`session detected ${session.session_ref}`);
      this.stopObserver();
      this.releaseLive();
      this.followed = session;
      this.leftActiveAt = null;
      this.notice = null;
      this.deps.activate();
    } else {
      if (this.followed!.reference.version !== session.reference.version) {
        this.deps.log.info('reference changed: semaphore window reset');
        this.stabilizer.reset();
      }
      if (this.followed!.phase_code !== session.phase_code) {
        this.deps.log.info(`phase ${this.followed!.phase_code} -> ${session.phase_code}`);
        this.stabilizer.reset();
      }
      this.followed = session;
    }
    this.lastStatus = session.status_code;
    this.appState = 'monitoring';
    if (session.capture_mode === 'vision' && session.live_available) {
      this.ensureObserver(session.session_id);
    } else {
      this.stopObserver();
      this.link = 'unavailable';
      this.linkDetail = session.capture_mode !== 'vision' ? 'manual_mode' : 'live_disabled';
    }
  }

  private onAmbiguous(overview: StationOverview): void {
    this.stopObserver();
    this.releaseLive();
    this.followed = null;
    this.appState = 'ambiguous';
    this.candidates = overview.candidates.map((c) => ({ ref: c.session_ref, startedAt: c.started_at }));
    this.notice = 'Hay más de una sesión en curso para esta estación. No se selecciona ninguna: el Operador debe dejar solo una activa en el portal.';
  }

  private async onIdle(): Promise<void> {
    this.candidates = [];
    if (!this.followed) {
      this.appState = 'waiting';
      return;
    }
    // The session we followed is no longer in_progress: read its official outcome.
    this.stopObserver();
    this.releaseLive();
    try {
      const latest = await this.deps.api.sessionMonitor(this.deps.stationId(), this.followed.session_id);
      if (latest.status_code !== this.lastStatus) {
        this.deps.log.info(`session ${latest.session_ref} status ${this.lastStatus} -> ${latest.status_code}`);
        this.leftActiveAt = this.now();
        this.lastStatus = latest.status_code;
        this.stabilizer.reset();
      }
      this.followed = latest;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        this.followed = null;
        this.appState = 'waiting';
        return;
      }
      throw err;
    }
    if (this.leftActiveAt === null) this.leftActiveAt = this.now();
    const phase = uiPhaseFor(this.followed.status_code, this.followed.phase_code);
    if (phase !== 'review' && phase !== 'ended') {
      // e.g. returned to scheduled (should not happen): nothing to show.
      this.followed = null;
      this.appState = 'waiting';
      return;
    }
    const hold = phase === 'review' ? this.opts.reviewHoldMs : this.opts.endedHoldMs;
    if (this.now() - this.leftActiveAt > hold) {
      this.deps.log.info(`session ${this.followed.session_ref} no longer shown (hold elapsed)`);
      this.followed = null;
      this.leftActiveAt = null;
      this.appState = 'waiting';
      this.deps.idle();
      return;
    }
    this.appState = phase;
  }

  private onError(err: unknown): void {
    if (err instanceof ApiError && !err.isNetwork) {
      if (err.status === 401) {
        this.deps.log.warn(`authorisation lost: ${err.code}`);
        this.signedOutInternal('La autorización de la estación expiró o fue revocada. Inicie sesión de nuevo.');
        return;
      }
      this.stopObserver();
      this.releaseLive();
      this.followed = null;
      this.appState = 'error';
      this.backend = 'ok';
      if (err.status === 403) this.notice = 'La cuenta no tiene permiso de Operador de estación.';
      else if (err.status === 404) this.notice = 'Estación no encontrada o no pertenece a su institución. Revise la configuración.';
      else if (err.code === 'station_disabled') this.notice = 'La estación está deshabilitada en Administración.';
      else this.notice = `Error del servidor (${err.status} ${err.code}).`;
      this.deps.log.warn(`station poll refused ${err.status} ${err.code}`);
      return;
    }
    this.failures += 1;
    this.backend = 'reconnecting';
    this.notice = 'Sin conexión con el servidor de sesiones. Reintentando…';
    if (this.failures === 1 || this.failures % 10 === 0) {
      this.deps.log.warn(`backend unreachable (${this.failures})`, err instanceof Error ? err.message : err);
    }
  }

  private signedOutInternal(message: string): void {
    this.stopObserver();
    this.releaseLive();
    this.followed = null;
    this.appState = 'login_required';
    this.backend = 'unauthorized';
    this.notice = message;
  }

  // --- live observer --------------------------------------------------------------------------------------

  private ensureObserver(sessionId: string): void {
    if (this.observer && this.observer.sessionId === sessionId) return;
    this.stopObserver();
    let serverHint: string | null = null;
    const stationId = this.deps.stationId();
    this.observer = new VisionObserver({
      sessionId,
      getTicket: async () => {
        const ticket = await this.deps.api.liveTicket(stationId, sessionId);
        serverHint = ticket.ws_url;
        return ticket;
      },
      resolveUrl: () => this.deps.resolveWsUrl(serverHint),
      wsFactory: this.deps.wsFactory,
      onState: (msg) => this.onWorkerState(sessionId, msg),
      onStatus: (status, detail) => this.onObserverStatus(sessionId, status, detail),
      log: (m) => this.deps.log.info(m),
      backoffMs: this.opts.observerBackoffMs,
      watchdogMs: this.opts.observerWatchdogMs,
    });
    this.observer.start();
  }

  private stopObserver(): void {
    if (this.observer) {
      const obs = this.observer;
      this.observer = null;
      obs.stop();
    }
    this.link = 'idle';
    this.linkDetail = null;
  }

  /** Session over / switched / revoked: drop frames, detections and readings (memory released). */
  private releaseLive(): void {
    this.frame = null;
    this.detections = [];
    this.readings = [];
    this.workerStatus = null;
    this.captureSource = null;
    this.capturePhase = null;
    this.recordingRunId = null;
    this.observedRunId = null;
    this.frameAgeS = null;
    this.lastUpdateAt = null;
    this.stabilizer.reset();
  }

  private onObserverStatus(sessionId: string, status: ObserverStatus, detail?: string): void {
    if (!this.observer || this.observer.sessionId !== sessionId) return;
    const map: Record<ObserverStatus, MonitorSnapshot['vision']['link']> = {
      connecting: 'connecting',
      connected: 'connected',
      reconnecting: 'reconnecting',
      unavailable: 'unavailable',
      unauthorized: 'unauthorized',
      stopped: 'idle',
    };
    this.link = map[status];
    this.linkDetail = detail ?? null;
    if (status !== 'connected') {
      // Readings from before a loss never count towards the next colour.
      this.readings = [];
      this.stabilizer.reset();
    }
    this.scheduleEmit();
  }

  private onWorkerState(sessionId: string, msg: MonitorStateMessage): void {
    if (!this.followed || this.followed.session_id !== sessionId || msg.session_id !== sessionId) return;
    const incomingRunId = msg.worker?.run_id ?? null;
    if (incomingRunId && this.observedRunId && incomingRunId !== this.observedRunId) {
      this.frame = null;
      this.detections = [];
      this.readings = [];
      this.recordingRunId = null;
      this.stabilizer.reset();
    }
    if (incomingRunId) this.observedRunId = incomingRunId;
    this.workerStatus = msg.worker?.status ?? null;
    this.captureSource = msg.worker?.capture_source ?? null;
    this.capturePhase = msg.worker?.capture_phase ?? null;
    this.recordingRunId = msg.worker?.recording_run_id ?? null;
    this.frameAgeS = typeof msg.worker?.frame_age_s === 'number' ? msg.worker.frame_age_s : null;
    if (this.workerStatus === 'unbound') {
      this.readings = [];
      this.detections = [];
      this.frame = null;
    }
    if (msg.frame && typeof msg.frame.jpeg_b64 === 'string') {
      const runId = msg.frame.run_id ?? msg.worker.run_id ?? null;
      const frames = Number(msg.frame.frames_processed ?? msg.worker.frames_processed ?? 0);
      const last = this.readings[this.readings.length - 1];
      const newer = !last || last.runId !== runId || frames > last.framesProcessed;
      if (newer) {
        const perFamily: Record<string, number> = {};
        for (const c of msg.counts_by_family ?? []) {
          if (c && typeof c.family_code === 'string') perFamily[c.family_code] = Math.max(0, Math.floor(Number(c.detected_quantity) || 0));
        }
        // Physical boxes the worker could not attribute to a counted family (no family, or a family absent
        // from counts_by_family): they are physical material, but WHICH family is unknown.
        const unassigned = (msg.detections ?? []).filter((d) => !d.family_code || !(d.family_code in perFamily)).length;
        const reading: Reading = {
          key: `${runId}:${frames}`,
          runId,
          framesProcessed: frames,
          receivedAt: this.now(),
          frameAgeS: this.frameAgeS,
          perFamily,
          unassigned,
          source: this.captureSource,
          workerStatus: this.workerStatus,
        };
        this.readings.push(reading);
        if (this.readings.length > MAX_READINGS) this.readings.splice(0, this.readings.length - MAX_READINGS);
        const mime = (msg.frame as { mime?: string }).mime ?? 'image/jpeg';
        if (ALLOWED_FRAME_MIME.has(mime) && /^[A-Za-z0-9+/=]+$/.test(msg.frame.jpeg_b64.slice(0, 4096))) {
          this.frame = { dataUrl: `data:${mime};base64,${msg.frame.jpeg_b64}`, key: reading.key };
        }
        this.detections = Array.isArray(msg.detections) ? msg.detections.slice(0, 200) : [];
        this.lastUpdateAt = new Date(this.now() - (this.frameAgeS ?? 0) * 1000).toISOString();
        // Consume every inference for colour stabilization. UI snapshots are throttled and may
        // otherwise skip consecutive readings that arrived between two renderer updates.
        this.buildSnapshot();
      }
      // else: duplicate or out-of-order frame → ignored
    }
    this.scheduleEmit();
  }

  // --- snapshot -------------------------------------------------------------------------------------------

  private sourceKind(): { kind: SourceKind; label: string } {
    if (this.deps.demo) return { kind: 'demo', label: 'Fuente simulada (modo demostración)' };
    if (this.captureSource === 'file') return { kind: 'recorded', label: 'Video pregrabado (subido por el Operador)' };
    if (this.captureSource === 'live') {
      const cam = this.followed?.camera;
      if (cam?.simulated) return { kind: 'simulated', label: `Fuente simulada: ${cam.name}` };
      if (cam && /virtual/i.test(cam.device_type)) return { kind: 'virtual', label: `Cámara virtual: ${cam.name}` };
      if (cam) return { kind: 'physical', label: `Cámara registrada: ${cam.name} (${cam.device_type})` };
      return { kind: 'unknown', label: 'Cámara del portal (dispositivo no registrado en la sesión)' };
    }
    return { kind: 'unknown', label: 'Sin fuente activa' };
  }

  private phase(): UiPhase {
    if (!this.followed) return 'none';
    return uiPhaseFor(this.followed.status_code, this.followed.phase_code);
  }

  buildSnapshot(): MonitorSnapshot {
    const now = this.now();
    const phase = this.phase();
    const s = this.followed;
    const raw = evaluateRaw({
      now,
      phase,
      statusCode: s?.status_code ?? null,
      backend: this.backend,
      session: s,
      live: { link: this.link, workerStatus: this.workerStatus, source: this.captureSource, capturePhase: this.capturePhase, readings: this.readings },
      config: this.opts.semaphore,
    });
    const view = this.stabilizer.push(raw, now);
    this.lastView = view;
    const latest = this.readings[this.readings.length - 1] ?? null;
    const usable = raw.readingKey !== null && (phase === 'initial' || phase === 'procedure' || phase === 'final');
    const fam = familyRows(s?.reference.families ?? [], latest, usable);
    const reg = this.overview?.camera_registry;
    const src = this.sourceKind();
    const ageS = latest && (this.workerStatus === 'processing') ? Math.max(0, (now - latest.receivedAt) / 1000 + (latest.frameAgeS ?? 0)) : this.frameAgeS;
    return {
      locale: this.deps.api.identity?.locale ?? 'es-MX',
      appState: this.appState,
      demo: Boolean(this.deps.demo),
      phase,
      phaseLabel: phaseLabel(phase, s?.status_code),
      station: this.overview
        ? { id: this.overview.station.id, name: this.overview.station.name, roomName: this.overview.station.room_name }
        : null,
      user: this.deps.api.identity ? { email: this.deps.api.identity.email } : null,
      connection: { backend: this.backend, lastSyncAt: this.lastSyncAt, message: this.notice },
      camera: {
        registered: (reg?.cameras ?? []).filter((c) => c.active).map((c) => ({ name: c.name, deviceType: c.device_type, simulated: c.simulated })),
        lastReported: reg?.last_report?.camera_reported ?? null,
        lastReportedAt: reg?.last_report?.camera_reported_at ?? null,
        localVideoInputs: this.localVideoInputs,
      },
      vision: {
        link: this.link,
        workerStatus: this.workerStatus,
        source: src.kind,
        sourceLabel: src.label,
        frameAgeS: ageS === null || ageS === undefined ? null : Math.round(ageS * 10) / 10,
        lastUpdateAt: this.lastUpdateAt,
        message: this.linkDetail,
      },
      session: s
        ? {
            id: s.session_id,
            ref: s.session_ref,
            procedureName: s.procedure_name,
            procedureCode: s.procedure_code,
            patientName: s.patient_name,
            doctorName: s.doctor_name,
            statusCode: s.status_code,
            statusName: s.status_name,
            phaseCode: s.phase_code,
            phaseName: s.phase_name,
            startedAt: s.started_at,
            captureMode: s.capture_mode,
          }
        : null,
      frame: (phase === 'initial' || phase === 'procedure' || phase === 'final') &&
        (this.workerStatus === 'processing' || this.workerStatus === 'ready') ? this.frame : null,
      recording: this.captureSource === 'file' && this.recordingRunId && this.followed && this.link === 'connected'
        ? { runId: this.recordingRunId, url: `pef-app://monitor/recording/${this.recordingRunId}`,
            capturePhase: this.capturePhase } : null,
      detections: this.detections,
      semaphore: view,
      families: fam.rows,
      familyAttributionReliable: fam.reliable,
      reference: s ? { total: s.reference.total, version: s.reference.version } : null,
      official: s ? s.official : null,
      notice: this.notice,
      candidates: this.candidates,
      autostart: this.deps.autostart(),
      generatedAt: new Date(now).toISOString(),
    };
  }

  get lastSemaphore(): SemaphoreView | null {
    return this.lastView;
  }

  get state(): AppState {
    return this.appState;
  }

  private emitNow(): void {
    if (this.emitPending) {
      clearTimeout(this.emitPending);
      this.emitPending = null;
    }
    this.lastEmit = this.now();
    this.deps.emit(this.buildSnapshot());
  }

  /** At most ~5 snapshots per second towards the renderer. */
  private scheduleEmit(): void {
    if (this.emitPending) return;
    const wait = Math.max(0, 200 - (this.now() - this.lastEmit));
    this.emitPending = setTimeout(() => {
      this.emitPending = null;
      this.emitNow();
    }, wait);
  }
}
