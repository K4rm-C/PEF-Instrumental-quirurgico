import type { BackendSession, MonitorStateMessage, Reading, StationOverview } from '../src/shared/types';

export const STATION = '11111111-1111-4111-8111-111111111111';
export const OTHER_STATION = '22222222-2222-4222-8222-222222222222';
export const SID = '33333333-3333-4333-8333-333333333333';
export const SID2 = '44444444-4444-4444-8444-444444444444';

export const FAMILIES = [
  { family_id: 'f-a', family_code: 'kelly', family_name: 'Pinza Kelly', expected_quantity: 4 },
  { family_id: 'f-b', family_code: 'mayo', family_name: 'Tijera Mayo', expected_quantity: 3 },
];
export const FULL = { kelly: 4, mayo: 3 };

export function session(over: Partial<BackendSession> = {}): BackendSession {
  return {
    session_id: SID,
    session_ref: SID.slice(0, 8).toUpperCase(),
    station_id: STATION,
    status_code: 'in_progress',
    status_name: 'In progress',
    phase_code: 'demo_phase_1',
    phase_name: 'Demo phase one',
    phase_changed_at: null,
    capture_mode: 'vision',
    procedure_name: null,
    procedure_code: null,
    patient_name: null,
    doctor_name: null,
    started_at: '2026-11-18T10:00:00+00:00',
    ended_at: null,
    updated_at: null,
    camera: null,
    reference: { total: 7, version: 'v1', families: FAMILIES },
    official: { result: 'pending', validated: false, validated_at: null, close_path: null, open_discrepancies: 0, discrepancies_total: 0 },
    live_available: true,
    ...over,
  };
}

export function overview(state: StationOverview['state'], s: BackendSession | null = null, over: Partial<StationOverview> = {}): StationOverview {
  return {
    server_time: new Date().toISOString(),
    station: { id: STATION, name: 'Estación 1', room_code: 'Q1', room_name: 'Quirófano 1' },
    camera_registry: { registry_available: true, cameras: [], last_report: null },
    state,
    session: s,
    candidates: [],
    live_monitor_enabled: true,
    ...over,
  };
}

export function reading(perFamily: Record<string, number>, over: Partial<Reading> = {}): Reading {
  const frames = over.framesProcessed ?? 1;
  const runId = over.runId ?? 'run1';
  return {
    key: `${runId}:${frames}`,
    runId,
    framesProcessed: frames,
    receivedAt: over.receivedAt ?? 0,
    frameAgeS: 0.1,
    perFamily,
    unassigned: 0,
    source: 'live',
    workerStatus: 'processing',
    ...over,
  };
}

export function stateMsg(counts: Record<string, number>, frames: number, over: Partial<MonitorStateMessage> = {}): MonitorStateMessage {
  const detections = Object.entries(counts).flatMap(([code, n]) =>
    Array.from({ length: n }, () => ({ family_code: code, family_name: code, confidence: 0.9, high_confidence: true, held: false, bbox: [0, 0, 1, 1] as [number, number, number, number] })),
  );
  return {
    type: 'monitor.state',
    session_id: SID,
    sent_at: new Date().toISOString(),
    worker: { status: 'processing', capture_source: 'live', message: '', run_id: 'run1', frames_processed: frames, last_frame_at: null, frame_age_s: 0.05 },
    frame: { jpeg_b64: 'QUJD', frames_processed: frames, run_id: 'run1' },
    counts_by_family: Object.entries(counts).map(([family_code, detected_quantity]) => ({ family_code, detected_quantity })),
    detections,
    ...over,
  };
}
