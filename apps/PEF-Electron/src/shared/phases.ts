// Mapping of the REAL backend states to the monitor's conceptual phases.
// Source catalogs: data/seeds/002_seed_catalogs.sql
//   cat_session_status: scheduled, in_progress, or_hold, awaiting_spd_review, correction_required, closed, aborted
//   cat_operation_phase: start, demo_phase_1, demo_phase_2, demo_phase_3, final_count
// Phase changes come only from the Operator's official flow (portal /capture/phase); the monitor never infers
// that surgery ended from what the camera sees.
import type { UiPhase } from './types';

export const REVIEW_STATUSES = new Set(['awaiting_spd_review', 'correction_required', 'or_hold']);
export const ENDED_STATUSES = new Set(['closed', 'aborted']);
export const INITIAL_PHASE = 'start';
export const FINAL_PHASE = 'final_count';

export function uiPhaseFor(statusCode: string | null | undefined, phaseCode: string | null | undefined): UiPhase {
  if (!statusCode) return 'none';
  if (statusCode === 'in_progress') {
    if (!phaseCode || phaseCode === INITIAL_PHASE) return 'initial';
    if (phaseCode === FINAL_PHASE) return 'final';
    return 'procedure';
  }
  if (REVIEW_STATUSES.has(statusCode)) return 'review';
  if (ENDED_STATUSES.has(statusCode)) return 'ended';
  return 'none'; // scheduled or unknown: not an active session for the monitor
}

export const PHASE_LABELS: Record<UiPhase, string> = {
  none: 'Esperando sesión',
  initial: 'Conteo inicial',
  procedure: 'Procedimiento en curso',
  final: 'Verificación final',
  review: 'Validación y revisión',
  ended: 'Sesión terminada',
};

export function phaseLabel(phase: UiPhase, statusCode?: string | null): string {
  if (phase === 'ended' && statusCode === 'aborted') return 'Sesión abortada';
  if (phase === 'review' && statusCode === 'correction_required') return 'Revisión: corrección requerida';
  if (phase === 'review' && statusCode === 'or_hold') return 'Revisión: retención en quirófano';
  return PHASE_LABELS[phase];
}
