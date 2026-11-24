import { describe, expect, it } from 'vitest';
import { DEFAULT_SEMAPHORE_CONFIG, UiStabilizer, evaluateRaw, familyRows, type EvalInput } from '../src/shared/semaphore';
import { uiPhaseFor } from '../src/shared/phases';
import { FAMILIES, FULL, reading, session } from './fixtures';
import type { Reading, UiPhase } from '../src/shared/types';

const NOW = 100_000;

function input(phase: UiPhase, readings: Reading[], over: Partial<EvalInput> = {}): EvalInput {
  return {
    now: NOW,
    phase,
    statusCode: 'in_progress',
    backend: 'ok',
    session: session(),
    live: { link: 'connected', workerStatus: 'processing', source: 'live', readings },
    ...over,
  };
}

/** Three fresh readings with the same counts, the last one at NOW. */
function series(counts: Record<string, number>, n = 3, start = 1): Reading[] {
  return Array.from({ length: n }, (_, i) => reading(counts, { framesProcessed: start + i, receivedAt: NOW - (n - 1 - i) * 400 }));
}

describe('phases (real backend catalogs)', () => {
  it('maps statuses and phases', () => {
    expect(uiPhaseFor('in_progress', 'start')).toBe('initial');
    expect(uiPhaseFor('in_progress', null)).toBe('initial');
    expect(uiPhaseFor('in_progress', 'demo_phase_2')).toBe('procedure');
    expect(uiPhaseFor('in_progress', 'final_count')).toBe('final');
    expect(uiPhaseFor('awaiting_spd_review', 'final_count')).toBe('review');
    expect(uiPhaseFor('correction_required', null)).toBe('review');
    expect(uiPhaseFor('or_hold', null)).toBe('review');
    expect(uiPhaseFor('closed', null)).toBe('ended');
    expect(uiPhaseFor('aborted', null)).toBe('ended');
    expect(uiPhaseFor('scheduled', null)).toBe('none');
    expect(uiPhaseFor(null, null)).toBe('none');
  });
});

describe('raw evaluation', () => {
  it('green provisional when the count matches the reference', () => {
    const r = evaluateRaw(input('procedure', series(FULL)));
    expect(r).toMatchObject({ color: 'green', kind: 'provisional', observed: 7, reference: 7, missing: 0 });
    expect(r.message).toMatch(/No autoriza el cierre/);
  });

  it('green is never confirmed during the initial count', () => {
    const r = evaluateRaw(input('initial', series(FULL)));
    expect(r.color).toBe('green');
    expect(r.kind).toBe('provisional');
  });

  it('green confirmed only from the official result', () => {
    const s = session({ status_code: 'awaiting_spd_review', official: { ...session().official, result: 'validated_match', validated: true } });
    const r = evaluateRaw(input('review', [], { session: s, statusCode: 'awaiting_spd_review' }));
    expect(r).toMatchObject({ color: 'green', kind: 'confirmed', immediate: true });
  });

  it('yellow when instruments are missing during the procedure (not a confirmed loss)', () => {
    const r = evaluateRaw(input('procedure', series({ kelly: 2, mayo: 3 })));
    expect(r).toMatchObject({ color: 'yellow', missing: 2, reason: 'missing_procedure' });
    expect(r.message).toMatch(/no es una pérdida confirmada/);
  });

  it('red when instruments are missing in the official final verification', () => {
    const r = evaluateRaw(input('final', series({ kelly: 3, mayo: 3 })));
    expect(r).toMatchObject({ color: 'red', missing: 1, reason: 'missing_final', kind: 'none' });
    expect(r.message).toBe('Instrumental faltante durante la verificación final. Requiere conciliación.');
  });

  it('surplus is an explicit discrepancy, never green', () => {
    const during = evaluateRaw(input('procedure', series({ kelly: 5, mayo: 3 })));
    expect(during).toMatchObject({ color: 'yellow', reason: 'surplus', surplus: 1 });
    const final = evaluateRaw(input('final', series({ kelly: 4, mayo: 3 }).map((r) => ({ ...r, unassigned: 1 }))));
    expect(final).toMatchObject({ color: 'red', reason: 'surplus', surplus: 1 });
  });

  it('same total but different families is not green', () => {
    const r = evaluateRaw(input('procedure', series({ kelly: 5, mayo: 2 })));
    expect(r).toMatchObject({ color: 'yellow', reason: 'family_mismatch' });
  });

  it.each([
    ['vision link down', { live: { link: 'reconnecting', workerStatus: 'processing', source: 'live', readings: series(FULL) } }, 'vision_disconnected'],
    ['worker forgot the session', { live: { link: 'connected', workerStatus: 'unbound', source: null, readings: [] } }, 'worker_unbound'],
    ['worker idle (no capture)', { live: { link: 'connected', workerStatus: 'idle', source: null, readings: series(FULL) } }, 'no_capture'],
    ['backend unreachable', { backend: 'reconnecting' }, 'backend_unreachable'],
    ['unauthorised', { backend: 'unauthorized' }, 'unauthorized'],
    ['manual capture mode', { session: session({ capture_mode: 'manual_no_privacy' }) }, 'manual_mode'],
    ['no reference', { session: session({ reference: { total: 0, version: 'x', families: [] } }) }, 'no_reference'],
    ['live monitor disabled', { session: session({ live_available: false }) }, 'live_disabled'],
  ] as const)('gray: %s', (_name, over, reason) => {
    const r = evaluateRaw(input('procedure', series(FULL), over as Partial<EvalInput>));
    expect(r.color).toBe('gray');
    expect(r.reason).toBe(reason);
    expect(r.immediate).toBe(true);
  });

  it('gray when there is no session', () => {
    expect(evaluateRaw(input('none', [], { session: null })).reason).toBe('no_session');
  });

  it('gray for stale data instead of showing an old count as current', () => {
    const old = series(FULL).map((r) => ({ ...r, receivedAt: r.receivedAt - 10_000 }));
    const r = evaluateRaw(input('procedure', old));
    expect(r).toMatchObject({ color: 'gray', reason: 'stale' });
  });

  it('gray with insufficient readings', () => {
    expect(evaluateRaw(input('procedure', series(FULL, 2))).reason).toBe('insufficient');
    expect(evaluateRaw(input('procedure', [])).reason).toBe('insufficient');
  });

  it('a finished uploaded video keeps its last result, labelled as not live', () => {
    const r = evaluateRaw(input('procedure', [reading(FULL, { receivedAt: NOW - 60_000, source: 'file' })], {
      live: { link: 'connected', workerStatus: 'ready', source: 'file', capturePhase: 'demo_phase_1', readings: [reading(FULL, { receivedAt: NOW - 60_000, source: 'file' })] },
    }));
    expect(r.color).toBe('green');
    expect(r.message).toMatch(/no en vivo/);
    expect(new UiStabilizer().push(r, NOW)).toMatchObject({ color: 'green', stabilizing: false });
  });

  it('a restarted monitor paints the historical shortage from one completed snapshot', () => {
    const captured = reading({ ...FULL, kelly: 3 }, { source: 'file', receivedAt: NOW - 60_000 });
    const raw = evaluateRaw(input('initial', [captured], {
      live: { link: 'connected', workerStatus: 'ready', source: 'file', capturePhase: 'start', readings: [captured] },
    }));
    expect(new UiStabilizer().push(raw, NOW)).toMatchObject({ color: 'yellow', observed: 6, stabilizing: false });
  });

  it('a historical initial video remains yellow after the session enters final verification', () => {
    const videoReading = reading({ ...FULL, kelly: 3 }, { source: 'file' });
    const r = evaluateRaw(input('final', [videoReading], {
      live: { link: 'connected', workerStatus: 'ready', source: 'file', capturePhase: 'start', readings: [videoReading] },
    }));
    expect(r.color).toBe('yellow');
  });

  it('a final video with a missing instrument is red even if current phase changes', () => {
    const videoReading = reading({ ...FULL, kelly: 3 }, { source: 'file' });
    const r = evaluateRaw(input('procedure', [videoReading], {
      live: { link: 'connected', workerStatus: 'ready', source: 'file', capturePhase: 'final_count', readings: [videoReading] },
    }));
    expect(r.color).toBe('red');
  });

  it('official outcomes of review / ended', () => {
    const mk = (result: string, status: string) =>
      evaluateRaw(input(uiPhaseFor(status, null), [], {
        statusCode: status,
        session: session({ status_code: status, official: { ...session().official, result: result as never, open_discrepancies: 1 } }),
      }));
    expect(mk('discrepancy', 'correction_required')).toMatchObject({ color: 'red', reason: 'official_discrepancy' });
    expect(mk('closed_clean', 'closed')).toMatchObject({ color: 'green', kind: 'confirmed' });
    // Unresolved / reconciled after discrepancies is never painted as a successful close.
    expect(mk('closed_with_discrepancies', 'closed').color).toBe('gray');
    expect(mk('aborted', 'aborted').color).toBe('gray');
    expect(mk('pending', 'awaiting_spd_review').color).toBe('gray');
  });
});

describe('family rows', () => {
  it('per-family status when attribution is reliable', () => {
    const { rows, reliable } = familyRows(FAMILIES, reading({ kelly: 2, mayo: 3 }), true);
    expect(reliable).toBe(true);
    expect(rows.map((r) => r.status)).toEqual(['missing', 'ok']);
    expect(rows[0]).toMatchObject({ expected: 4, detected: 2, diff: -2 });
  });

  it('does not invent which family is missing when boxes are unclassified', () => {
    const { rows, reliable } = familyRows(FAMILIES, reading({ kelly: 2, mayo: 3 }, { unassigned: 2 }), true);
    expect(reliable).toBe(false);
    expect(rows[0].status).toBe('undetermined');
  });

  it('no data without a usable reading', () => {
    expect(familyRows(FAMILIES, null, false).rows.every((r) => r.status === 'no_data')).toBe(true);
  });
});

describe('visual stabiliser', () => {
  const cfg = { ...DEFAULT_SEMAPHORE_CONFIG, confirmReadings: 3, minHoldMs: 1000 };

  function feed(stab: UiStabilizer, counts: Record<string, number>, frames: number, at: number, phase: UiPhase = 'procedure') {
    const readings = series(counts, 3, frames - 2).map((r, i) => ({ ...r, receivedAt: at - (2 - i) * 100 }));
    return stab.push(evaluateRaw({ ...input(phase, readings), now: at, config: cfg }), at);
  }

  it('the first colour needs consecutive readings and time (gray while stabilising)', () => {
    const stab = new UiStabilizer(cfg);
    expect(feed(stab, FULL, 3, 1000)).toMatchObject({ color: 'gray', stabilizing: true });
    expect(feed(stab, FULL, 4, 1400).color).toBe('gray');
    expect(feed(stab, FULL, 5, 1800).color).toBe('gray'); // 3 readings but only 800 ms
    expect(feed(stab, FULL, 6, 2200)).toMatchObject({ color: 'green', stabilizing: false });
  });

  it('a single odd frame does not make the light flicker', () => {
    const stab = new UiStabilizer(cfg);
    for (let f = 3, t = 0; f < 9; f++, t += 400) feed(stab, FULL, f, t);
    const blip = feed(stab, { kelly: 3, mayo: 3 }, 9, 2600);
    expect(blip).toMatchObject({ color: 'green', stabilizing: true });
    expect(feed(stab, FULL, 10, 3000)).toMatchObject({ color: 'green', stabilizing: false });
  });

  it('a sustained change is painted after confirmation', () => {
    const stab = new UiStabilizer(cfg);
    for (let f = 3, t = 0; f < 9; f++, t += 400) feed(stab, FULL, f, t);
    let last;
    for (let f = 9, t = 2400; f < 14; f++, t += 400) last = feed(stab, { kelly: 2, mayo: 3 }, f, t);
    expect(last).toMatchObject({ color: 'yellow', missing: 2 });
  });

  it('out-of-order and repeated frames give no extra evidence', () => {
    const stab = new UiStabilizer(cfg);
    for (let f = 3, t = 0; f < 9; f++, t += 400) feed(stab, FULL, f, t);
    // The same (old) yellow frame delivered many times never confirms yellow.
    for (let i = 0, t = 3000; i < 6; i++, t += 400) {
      expect(feed(stab, { kelly: 2, mayo: 3 }, 5, t).color).toBe('green');
    }
    expect(stab.acceptsReading('run1', 4)).toBe(false);
    expect(stab.acceptsReading('run2', 1)).toBe(true); // a new run starts its own sequence
  });

  it('safety gray is immediate: never keeps green when the system cannot verify', () => {
    const stab = new UiStabilizer(cfg);
    for (let f = 3, t = 0; f < 9; f++, t += 400) feed(stab, FULL, f, t);
    const lost = stab.push(evaluateRaw({ ...input('procedure', series(FULL)), live: { link: 'reconnecting', workerStatus: 'processing', source: 'live', readings: [] } }), 3000);
    expect(lost).toMatchObject({ color: 'gray', reason: 'vision_disconnected', stabilizing: false });
  });

  it('reset (reference or phase change) requires new confirmation', () => {
    const stab = new UiStabilizer(cfg);
    for (let f = 3, t = 0; f < 9; f++, t += 400) feed(stab, FULL, f, t);
    stab.reset();
    expect(feed(stab, FULL, 9, 3000).color).toBe('gray');
  });

  it('entering the final verification with a shortage turns red after confirmation', () => {
    const stab = new UiStabilizer(cfg);
    let v;
    for (let f = 3, t = 0; f < 10; f++, t += 400) v = feed(stab, { kelly: 3, mayo: 3 }, f, t, 'final');
    expect(v).toMatchObject({ color: 'red', reason: 'missing_final' });
  });
});
