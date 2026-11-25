import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MonitorScreen } from '../src/renderer/components/Monitor';
import { phaseText, procedureText, translate } from '../src/shared/i18n';
import type { MonitorSnapshot } from '../src/shared/types';

const snapshot: MonitorSnapshot = {
  locale: 'es-MX', appState: 'monitoring', demo: false, phase: 'initial', phaseLabel: 'Conteo inicial',
  station: { id: 'station', name: 'Estación 1', roomName: 'Quirófano 1' },
  user: { email: 'operator@example.test' },
  connection: { backend: 'ok', lastSyncAt: null, message: null },
  camera: { registered: [], lastReported: null, lastReportedAt: null, localVideoInputs: null },
  vision: { link: 'connected', workerStatus: 'processing', source: 'physical', sourceLabel: 'Cámara del portal (dispositivo no registrado en la sesión)', frameAgeS: null, lastUpdateAt: null, message: null },
  session: { id: 'session', ref: 'ABC12345', patientName: 'María García', procedureName: 'Open appendectomy',
    procedureCode: 'appendectomy', doctorName: 'Dra. Ana Ruiz', statusCode: 'in_progress', statusName: 'In progress',
    phaseCode: 'start', phaseName: 'Start', startedAt: null, captureMode: 'vision' },
  frame: null, recording: null, detections: [], families: [], familyAttributionReliable: true,
  reference: { total: 5, version: 'one' },
  semaphore: { color: 'gray', kind: 'none', icon: 'unknown', title: 'Sin captura',
    message: 'El Operador no está transmitiendo cámara ni procesando video en este momento.', reason: 'no_capture',
    observed: null, reference: null, missing: null, surplus: null, stabilizing: false, immediate: true, readingKey: null },
  official: null, notice: null, candidates: [], autostart: false, generatedAt: '2026-11-24T00:00:00Z',
};

describe('renderer locale and clinical details', () => {
  it('shows patient, procedure, and surgeon below the video and semaphore in Spanish', () => {
    const html = renderToStaticMarkup(createElement(MonitorScreen, { snap: snapshot }));
    expect(html.indexOf('class="session-details"')).toBeGreaterThan(html.indexOf('class="monitor-main"'));
    expect(html).toContain('Paciente');
    expect(html).toContain('María García');
    expect(html).toContain('Apendicectomía abierta');
    expect(html).toContain('Dra. Ana Ruiz');
  });

  it('uses English labels while preserving patient and physician names', () => {
    const html = renderToStaticMarkup(createElement(MonitorScreen, { snap: { ...snapshot, locale: 'en' } }));
    expect(html).toContain('Patient');
    expect(html).toContain('Procedure');
    expect(html).toContain('Doctor');
    expect(html).toContain('Open appendectomy');
    expect(html).toContain('María García');
    expect(html).toContain('Dra. Ana Ruiz');
    expect(html).toContain('No capture');
  });

  it('localizes known catalog procedures and keeps custom names', () => {
    expect(procedureText('es-MX', 'cesarean', 'Cesarean section')).toBe('Cesárea');
    expect(procedureText('en', 'cesarean', 'Cesárea')).toBe('Cesarean section');
    expect(procedureText('en', 'custom', 'Custom operation')).toBe('Custom operation');
    expect(phaseText('en', 'final')).toBe('Final verification');
    expect(translate('en', 'Sin conexión')).toBe('Disconnected');
  });
});
