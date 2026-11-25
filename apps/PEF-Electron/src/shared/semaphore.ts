// Intelligent traffic light (ELECTRON-01, phase 5). VISUAL ONLY.
//
// Sources of truth:
// - official status, phase, reference and validation outcome: SessionService (PostgreSQL);
// - live count: VisionWorker (the same inference the Operator's capture produces).
// Nothing computed here is persisted, sent to the backend or used to accept/close anything. The stabiliser
// only decides which colour to *paint*; it never edits counts, frames, YOLO output, the reference or any rule.
import type {
  BackendSession,
  FamilyRow,
  GreenKind,
  LightColor,
  LightIcon,
  Reading,
  SemaphoreView,
  UiPhase,
} from './types';

export interface SemaphoreConfig {
  /** A reading older than this (receipt age + worker-reported frame age) is not current. */
  staleMs: number;
  /** Readings needed inside `windowMs` before any colour other than gray is shown. */
  minReadings: number;
  windowMs: number;
  /** Consecutive readings with the same colour needed to change the painted colour. */
  confirmReadings: number;
  /** ...and the minimum time that candidate colour must have persisted. */
  minHoldMs: number;
}

export const DEFAULT_SEMAPHORE_CONFIG: SemaphoreConfig = {
  staleMs: 6000,
  minReadings: 3,
  windowMs: 8000,
  confirmReadings: 3,
  minHoldMs: 1500,
};

export interface LiveInput {
  link: 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'unavailable' | 'unauthorized';
  workerStatus: string | null; // processing | ready | idle | error | unbound | null
  source: string | null; // live | file | null
  capturePhase?: string | null;
  readings: Reading[]; // newest last, already de-duplicated and ordered
}

export interface EvalInput {
  now: number;
  phase: UiPhase;
  statusCode: string | null;
  backend: 'ok' | 'reconnecting' | 'down' | 'unauthorized' | 'unknown';
  session: Pick<BackendSession, 'capture_mode' | 'reference' | 'official' | 'live_available'> | null;
  live: LiveInput;
  config?: SemaphoreConfig;
}

export interface RawEvaluation extends SemaphoreView {
  /** true = paint now (safety gray, official outcome); false = must pass the stabiliser. */
  immediate: boolean;
  readingKey: string | null;
}

function view(
  color: LightColor,
  kind: GreenKind,
  icon: LightIcon,
  title: string,
  message: string,
  reason: string,
  extra: Partial<RawEvaluation> = {},
): RawEvaluation {
  return {
    color,
    kind,
    icon,
    title,
    message,
    reason,
    observed: null,
    reference: null,
    missing: null,
    surplus: null,
    stabilizing: false,
    immediate: true,
    readingKey: null,
    ...extra,
  };
}

const gray = (reason: string, title: string, message: string, extra: Partial<RawEvaluation> = {}) =>
  view('gray', 'none', reason === 'no_session' ? 'pause' : 'unknown', title, message, reason, extra);

/** Physical total and per-family numbers of one reading against the current reference. */
export function compareReading(reading: Reading, families: { family_code: string; expected_quantity: number }[]) {
  const expectedCodes = new Set(families.map((f) => f.family_code));
  let assigned = 0;
  let foreign = 0;
  for (const [code, qty] of Object.entries(reading.perFamily)) {
    if (expectedCodes.has(code)) assigned += qty;
    else foreign += qty; // a family that is not in the reference counts as physical surplus material
  }
  const unassigned = reading.unassigned + foreign;
  const observed = assigned + unassigned;
  const reference = families.reduce((acc, f) => acc + f.expected_quantity, 0);
  const familyMismatch = families.some((f) => (reading.perFamily[f.family_code] ?? 0) !== f.expected_quantity);
  return { observed, reference, unassigned, familyMismatch };
}

export function evaluateRaw(input: EvalInput): RawEvaluation {
  const cfg = input.config ?? DEFAULT_SEMAPHORE_CONFIG;
  const { phase, session, live, now } = input;

  if (input.backend === 'unauthorized') {
    return gray('unauthorized', 'Sin autorización', 'La credencial de la estación no es válida o fue revocada. Inicie sesión de nuevo.');
  }
  if (phase === 'none' || !session) {
    return gray('no_session', 'Sin sesión activa', 'Esperando sesión del Operador.');
  }

  // --- Official outcomes (review / ended): mirrored from the backend, painted immediately -------------------
  if (phase === 'review' || phase === 'ended') {
    const o = session.official;
    switch (o.result) {
      case 'validated_match':
        return view('green', 'confirmed', 'check', 'Verde confirmado',
          'Validación oficial del Operador sin discrepancias. Pendiente de revisión y cierre por SPD.', 'official_validated');
      case 'closed_clean':
        return view('green', 'confirmed', 'check', 'Sesión cerrada', 'Cierre oficial sin discrepancias.', 'official_closed');
      case 'discrepancy':
        return view('red', 'none', 'stop', 'Discrepancia registrada',
          `Discrepancia oficial abierta (${o.open_discrepancies}). Requiere conciliación en el sistema web.`, 'official_discrepancy');
      case 'closed_with_discrepancies':
        return gray('official_closed_discrepancies', 'Cerrada con discrepancias',
          'La sesión se cerró después de discrepancias registradas. Consulte el resultado en el portal.');
      case 'aborted':
        return gray('official_aborted', 'Sesión abortada', 'La sesión fue abortada. No hay conteo confirmado.');
      default:
        return gray('official_pending', 'Esperando resultado oficial', 'El resultado oficial aún no está disponible.');
    }
  }

  // --- In progress (initial / procedure / final): provisional monitoring ------------------------------------
  if (input.backend !== 'ok') {
    return gray('backend_unreachable', 'Reconectando', 'Sin conexión con el servicio de sesiones: no se puede verificar la sesión.');
  }
  if (session.capture_mode !== 'vision') {
    return gray('manual_mode', 'Sesión sin visión', 'Esta sesión se cuenta manualmente (sin aviso de privacidad): no hay monitoreo por cámara.');
  }
  const reference = session.reference.total;
  if (!session.reference.families.length || reference <= 0) {
    return gray('no_reference', 'Sin referencia', 'La sesión no tiene inventario esperado vigente.');
  }
  if (!session.live_available) {
    return gray('live_disabled', 'Video no disponible', 'El monitor en vivo no está habilitado en el servidor.');
  }
  if (live.link === 'unauthorized') {
    return gray('vision_unauthorized', 'Video sin autorización', 'VisionWorker rechazó la autorización del monitor.');
  }
  if (live.link !== 'connected') {
    return gray('vision_disconnected', live.link === 'reconnecting' ? 'Reconectando video' : 'Conectando video',
      'Sin enlace con VisionWorker.');
  }
  if (live.workerStatus === 'unbound') {
    return gray('worker_unbound', 'VisionWorker sin la sesión',
      'El worker no tiene esta sesión cargada (¿reinicio?). El Operador debe reabrir la captura en el portal.');
  }
  if (live.workerStatus === 'error') {
    return gray('worker_error', 'Error en VisionWorker', 'La inferencia reportó un error.');
  }
  const latest = live.readings[live.readings.length - 1];
  if (live.workerStatus !== 'processing' && !(live.workerStatus === 'ready' && live.source === 'file')) {
    return gray('no_capture', 'Sin captura', 'El Operador no está transmitiendo cámara ni procesando video en este momento.');
  }
  if (!latest) {
    return gray('insufficient', 'Lecturas insuficientes', 'Aún no hay resultados de inferencia.');
  }
  const finishedVideo = live.workerStatus === 'ready' && live.source === 'file';
  if (live.source === 'file' && !live.capturePhase) {
    return gray('unknown_capture_phase', 'Fase de captura desconocida',
      'No se conoce la fase en que se subió este video. Consulte el resultado en el portal.');
  }
  const ageMs = now - latest.receivedAt + (latest.frameAgeS ?? 0) * 1000;
  if (!finishedVideo && ageMs > cfg.staleMs) {
    return gray('stale', 'Datos antiguos', `Sin resultados nuevos desde hace ${Math.round(ageMs / 1000)} s. No se muestra un conteo antiguo como actual.`);
  }
  const recent = live.readings.filter((r) => now - r.receivedAt <= cfg.windowMs);
  if (!finishedVideo && recent.length < cfg.minReadings) {
    return gray('insufficient', 'Lecturas insuficientes', 'Esperando lecturas consecutivas suficientes.');
  }

  const cmp = compareReading(latest, session.reference.families);
  const numbers = {
    observed: cmp.observed,
    reference: cmp.reference,
    missing: Math.max(0, cmp.reference - cmp.observed),
    surplus: Math.max(0, cmp.observed - cmp.reference),
    // A completed file is one immutable historical result. A restarted monitor receives one
    // snapshot, so waiting for fresh consecutive frames would leave it gray forever.
    immediate: finishedVideo,
    readingKey: latest.key,
  };
  // A submitted file is a historical capture. The current session phase may have advanced since upload.
  const isFinal = live.source === 'file' ? live.capturePhase === 'final_count' : phase === 'final';
  const videoNote = finishedVideo ? ' (resultado del video procesado, no en vivo)' : '';

  if (cmp.observed > cmp.reference) {
    return view(isFinal ? 'red' : 'yellow', 'none', isFinal ? 'stop' : 'alert', 'Sobrante observado',
      `Se observan ${numbers.surplus} pieza(s) más que la referencia${videoNote}. Discrepancia explícita: no es un conteo confirmado.`,
      'surplus', numbers);
  }
  if (cmp.observed < cmp.reference) {
    if (isFinal) {
      return view('red', 'none', 'stop', 'Faltante en verificación final',
        `Instrumental faltante durante la verificación final. Requiere conciliación.${videoNote}`, 'missing_final', numbers);
    }
    return view('yellow', 'none', 'alert', 'Faltan instrumentos en la charola',
      `Faltan ${numbers.missing} respecto a la referencia${videoNote}. Pueden estar en uso: no es una pérdida confirmada.`,
      'missing_procedure', numbers);
  }
  if (cmp.familyMismatch || cmp.unassigned > 0) {
    return view('yellow', 'none', 'alert', 'Clasificación difiere',
      `El total coincide, pero la clasificación por familia no coincide con la referencia${videoNote}. Requiere revisión.`,
      'family_mismatch', numbers);
  }
  return view('green', 'provisional', 'check', 'Verde provisional',
    `La cantidad observada coincide con la referencia${videoNote}. No autoriza el cierre: la validación oficial se realiza en el sistema web.`,
    'match', numbers);
}

/** Per-family rows. Family-level shortages are shown only when attribution is reliable. */
export function familyRows(
  families: { family_id: string; family_code: string; family_name: string; expected_quantity: number }[],
  reading: Reading | null,
  usable: boolean,
): { rows: FamilyRow[]; reliable: boolean } {
  if (!reading || !usable) {
    return {
      rows: families.map((f) => ({
        familyId: f.family_id, familyCode: f.family_code, familyName: f.family_name, expected: f.expected_quantity,
        detected: null, diff: null, status: 'no_data' as const,
      })),
      reliable: false,
    };
  }
  const cmp = compareReading(reading, families);
  // Unassigned / foreign boxes mean the model could not say WHICH family they are: do not invent it.
  const reliable = cmp.unassigned === 0;
  const rows = families.map((f) => {
    const detected = reading.perFamily[f.family_code] ?? 0;
    const diff = detected - f.expected_quantity;
    let status: FamilyRow['status'] = diff === 0 ? 'ok' : diff < 0 ? 'missing' : 'surplus';
    if (!reliable && diff !== 0) status = 'undetermined';
    return { familyId: f.family_id, familyCode: f.family_code, familyName: f.family_name, expected: f.expected_quantity, detected, diff, status };
  });
  return { rows, reliable };
}

/**
 * Visual stabiliser: a colour must be seen in `confirmReadings` consecutive NEW readings and persist `minHoldMs`
 * before it is painted. Safety grays and official outcomes (`immediate`) bypass it, so the light can never stay
 * green when the system cannot verify the situation. Out-of-order or repeated readings are ignored.
 */
export class UiStabilizer {
  private committed: RawEvaluation | null = null;
  private candidate: { color: LightColor; reason: string; count: number; since: number } | null = null;
  private lastKey: { runId: string | null; frames: number } | null = null;
  private readonly cfg: SemaphoreConfig;

  constructor(cfg: SemaphoreConfig = DEFAULT_SEMAPHORE_CONFIG) {
    this.cfg = cfg;
  }

  reset(): void {
    this.committed = null;
    this.candidate = null;
    this.lastKey = null;
  }

  /** True if a reading is newer than the last one consumed (same run: strictly more frames; new run: accepted). */
  acceptsReading(runId: string | null, frames: number): boolean {
    if (!this.lastKey) return true;
    if (runId !== this.lastKey.runId) return true;
    return frames > this.lastKey.frames;
  }

  push(raw: RawEvaluation, now: number): SemaphoreView {
    if (raw.immediate) {
      this.committed = raw;
      this.candidate = null;
      return { ...raw, stabilizing: false };
    }
    // Same reading evaluated again (timer tick): no new evidence for the candidate.
    const key = raw.readingKey;
    const [runId, framesStr] = key ? [key.slice(0, key.lastIndexOf(':')), key.slice(key.lastIndexOf(':') + 1)] : [null, '0'];
    const frames = Number(framesStr);
    const fresh = key !== null && this.acceptsReading(runId === 'null' ? null : runId, frames);
    if (fresh) this.lastKey = { runId: runId === 'null' ? null : runId, frames };

    // First coloured evaluation after a gray (or after reset): it still needs confirmation.
    const sameAsCommitted = this.committed && !this.committed.immediate && this.committed.color === raw.color && this.committed.reason === raw.reason;
    if (sameAsCommitted) {
      this.committed = raw; // numbers follow the latest reading of the same state
      this.candidate = null;
      return { ...raw, stabilizing: false };
    }
    if (fresh) {
      if (this.candidate && this.candidate.color === raw.color && this.candidate.reason === raw.reason) {
        this.candidate.count += 1;
      } else {
        this.candidate = { color: raw.color, reason: raw.reason, count: 1, since: now };
      }
    }
    const c = this.candidate;
    if (c && c.count >= this.cfg.confirmReadings && now - c.since >= this.cfg.minHoldMs) {
      this.committed = raw;
      this.candidate = null;
      return { ...raw, stabilizing: false };
    }
    if (this.committed && !this.committed.immediate) {
      return { ...this.committed, stabilizing: true };
    }
    // Nothing coloured confirmed yet: keep gray while the first colour stabilises.
    return {
      ...gray('stabilizing', 'Estabilizando lectura', 'Esperando lecturas consecutivas coherentes.'),
      observed: raw.observed,
      reference: raw.reference,
      missing: raw.missing,
      surplus: raw.surplus,
      stabilizing: true,
    };
  }
}
