// Shared contracts between the main process (source of every value) and the renderer (pure view).
// Backend payloads mirror apps/SessionService/services/station_monitor.py and
// apps/VisionWorker/live_monitor.py. Nothing here is a clinical rule.

export type UiPhase = 'none' | 'initial' | 'procedure' | 'final' | 'review' | 'ended';

export type AppState =
  | 'setup' // no station configuration yet
  | 'login_required' // no valid credentials (first run, expiry or revocation)
  | 'connecting' // first contact with the backend
  | 'waiting' // station authorised, no active session
  | 'ambiguous' // more than one active session: none is chosen
  | 'monitoring' // in_progress session followed
  | 'review' // session left in_progress: official review / validation outcome
  | 'ended' // closed / aborted
  | 'error'; // configuration or authorisation problem that needs a human

export type LightColor = 'green' | 'yellow' | 'red' | 'gray';
export type GreenKind = 'provisional' | 'confirmed' | 'none';
export type LightIcon = 'check' | 'alert' | 'stop' | 'unknown' | 'pause';

export type OfficialResult =
  | 'pending'
  | 'validated_match'
  | 'discrepancy'
  | 'closed_clean'
  | 'closed_with_discrepancies'
  | 'aborted';

export interface ReferenceFamily {
  family_id: string;
  family_code: string;
  family_name: string;
  expected_quantity: number;
}

export interface BackendSession {
  session_id: string;
  session_ref: string;
  station_id: string;
  status_code: string;
  status_name: string | null;
  phase_code: string | null;
  phase_name: string | null;
  phase_changed_at: string | null;
  capture_mode: string | null;
  procedure_name: string | null;
  procedure_code: string | null;
  patient_name: string | null;
  doctor_name: string | null;
  started_at: string | null;
  ended_at: string | null;
  updated_at: string | null;
  camera: { id: string; name: string; device_type: string; simulated: boolean } | null;
  reference: { total: number; version: string; families: ReferenceFamily[] };
  official: {
    result: OfficialResult;
    validated: boolean;
    validated_at: string | null;
    close_path: string | null;
    open_discrepancies: number;
    discrepancies_total: number;
  };
  live_available: boolean;
}

export interface StationOverview {
  server_time: string;
  station: { id: string; name: string; room_code: string | null; room_name: string | null };
  camera_registry: {
    registry_available: boolean;
    cameras: { id: string; name: string; device_type: string; simulated: boolean; active: boolean }[];
    last_report: {
      camera_reported: string;
      camera_reported_at: string | null;
      frames_seen_at: string | null;
      last_seen_at: string | null;
      client: string | null;
    } | null;
  };
  state: 'active' | 'idle' | 'ambiguous';
  session: BackendSession | null;
  candidates: { session_id: string; session_ref: string; started_at: string | null }[];
  live_monitor_enabled: boolean;
}

export interface LiveTicket {
  session_id: string;
  ticket: string;
  ws_url: string | null;
  expires_at: number;
  ttl_s: number;
  scope: 'observe';
}

export interface WorkerDetection {
  family_code: string;
  family_name: string;
  confidence: number;
  high_confidence: boolean;
  held: boolean;
  bbox: [number, number, number, number];
}

export interface MonitorStateMessage {
  type: 'monitor.state';
  session_id: string;
  sent_at: string | null;
  worker: {
    status: string | null;
    capture_source: string | null;
    capture_phase?: string | null;
    message: string;
    run_id: string | null;
    recording_run_id?: string | null;
    frames_processed: number;
    model_version?: string | null;
    last_frame_at: string | null;
    frame_age_s: number | null;
    progress?: number | null;
  };
  // `mime` is only set by the demo source (SVG); VisionWorker always sends JPEG.
  frame: { jpeg_b64: string; frames_processed: number; run_id: string | null; mime?: string } | null;
  counts_by_family: { family_code: string; family_id?: string | null; detected_quantity: number }[];
  detections: WorkerDetection[];
}

/** One inference result as seen by the monitor (only built from messages carrying a NEW frame). */
export interface Reading {
  key: string; // `${runId}:${framesProcessed}`
  runId: string | null;
  framesProcessed: number;
  receivedAt: number; // client clock, ms
  frameAgeS: number | null; // worker-reported age at send time
  perFamily: Record<string, number>; // family_code -> detected
  unassigned: number; // boxes with no expected family
  source: string | null; // file | live
  workerStatus: string | null;
}

export interface FamilyRow {
  familyId: string;
  familyCode: string;
  familyName: string;
  expected: number;
  detected: number | null;
  diff: number | null;
  status: 'ok' | 'missing' | 'surplus' | 'undetermined' | 'no_data';
}

export interface SemaphoreView {
  color: LightColor;
  kind: GreenKind;
  icon: LightIcon;
  title: string;
  message: string;
  reason: string;
  observed: number | null;
  reference: number | null;
  missing: number | null;
  surplus: number | null;
  stabilizing: boolean;
}

export type SourceKind = 'physical' | 'virtual' | 'simulated' | 'recorded' | 'demo' | 'unknown';

export interface MonitorSnapshot {
  locale: 'en' | 'es-MX';
  appState: AppState;
  demo: boolean;
  phase: UiPhase;
  phaseLabel: string;
  station: { id: string; name: string; roomName: string | null } | null;
  user: { email: string } | null;
  connection: {
    backend: 'ok' | 'reconnecting' | 'down' | 'unauthorized' | 'unknown';
    lastSyncAt: string | null;
    message: string | null;
  };
  camera: {
    registered: { name: string; deviceType: string; simulated: boolean }[];
    lastReported: string | null;
    lastReportedAt: string | null;
    localVideoInputs: number | null;
  };
  vision: {
    link: 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'unavailable' | 'unauthorized';
    workerStatus: string | null;
    source: SourceKind;
    sourceLabel: string;
    frameAgeS: number | null;
    lastUpdateAt: string | null;
    message: string | null;
  };
  session: {
    id: string;
    ref: string;
    procedureName: string | null;
    procedureCode: string | null;
    patientName: string | null;
    doctorName: string | null;
    statusCode: string;
    statusName: string | null;
    phaseCode: string | null;
    phaseName: string | null;
    startedAt: string | null;
    captureMode: string | null;
  } | null;
  frame: { dataUrl: string; key: string } | null;
  recording: { runId: string; url: string; capturePhase: string | null } | null;
  detections: WorkerDetection[];
  semaphore: SemaphoreView;
  families: FamilyRow[];
  familyAttributionReliable: boolean;
  reference: { total: number; version: string } | null;
  official: BackendSession['official'] | null;
  notice: string | null;
  candidates: { ref: string; startedAt: string | null }[];
  autostart: boolean;
  generatedAt: string;
}

export interface PublicConfig {
  configured: boolean;
  apiBaseUrl: string;
  authBaseUrl: string;
  sessionBaseUrl: string;
  visionWsUrl: string;
  stationId: string;
  autostart: boolean;
  startHidden: boolean;
  hideWhenIdle: boolean;
  credentialsStored: boolean;
  secureStorage: boolean;
}
