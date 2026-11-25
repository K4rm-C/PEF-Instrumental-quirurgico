import type { MonitorSnapshot, SemaphoreView } from '../../shared/types';
import { Dot, PefLogo, StateIcon } from './Icons';
import { backendLabel, backendTone, clock, linkLabel, linkTone } from '../format';
import { phaseText, procedureText, translate as tr } from '../../shared/i18n';

const COLOR_NAME: Record<SemaphoreView['color'], string> = {
  green: 'Verde',
  yellow: 'Amarillo',
  red: 'Rojo',
  gray: 'Gris',
};

export function Header({ snap, onMenu }: { snap: MonitorSnapshot; onMenu: () => void }) {
  const locale = snap.locale;
  return (
    <header className="header">
      <div className="brand">
        <PefLogo size={38} />
        <div>
          <div className="brand-title">PEF Monitor</div>
          <div className="brand-sub">
            {snap.station ? `${snap.station.name}${snap.station.roomName ? ` · ${snap.station.roomName}` : ''}` : tr(locale, 'Estación sin verificar')}
          </div>
        </div>
      </div>
      <div className="header-mid">
        {snap.session && (
          <div className="session-id" title={snap.session.id}>
            <span className="muted">{tr(locale, 'Sesión')}</span> <strong>{snap.session.ref}</strong>
            {snap.session.procedureName && <span className="muted"> · {procedureText(locale, snap.session.procedureCode, snap.session.procedureName)}</span>}
          </div>
        )}
        <span className={`phase-chip phase-${snap.phase}`} data-testid="phase">{phaseText(locale, snap.phase, snap.session?.statusCode)}</span>
        {snap.demo && <span className="demo-chip">{tr(locale, 'DEMOSTRACIÓN · SIMULADO')}</span>}
      </div>
      <div className="header-right">
        <span className="conn" title={snap.connection.message ?? ''}>
          <Dot tone={backendTone(snap.connection.backend)} /> {backendLabel(snap.connection.backend, locale)}
        </span>
        <button className="icon-btn" onClick={onMenu} aria-label={tr(locale, 'Opciones')}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
      </div>
    </header>
  );
}

export function VideoPanel({ snap }: { snap: MonitorSnapshot }) {
  const locale = snap.locale;
  const v = snap.vision;
  const live = v.link === 'connected' && snap.semaphore.reason !== 'stale' && v.workerStatus !== 'unbound';
  const stale = snap.frame && !live;
  return (
    <section className="video-panel" aria-label={tr(locale, v.source === 'recorded' ? 'Captura de video pregrabado' : 'Captura de cámara')}>
      <div className="video-frame">
        {snap.recording ? (
          <video className="frame" key={snap.recording.runId} src={snap.recording.url}
            controls autoPlay muted playsInline aria-label={tr(locale, 'Video procesado con detecciones de YOLO')} />
        ) : snap.frame ? (
          <img className={`frame ${stale ? 'frame-stale' : ''}`} src={snap.frame.dataUrl} alt={tr(locale, 'Último fotograma procesado por VisionWorker con detecciones')} />
        ) : (
          <div className="frame-empty">
            <StateIcon icon="unknown" size={56} />
            <div>{tr(locale, videoEmptyText(snap))}</div>
          </div>
        )}
        {stale && v.source !== 'recorded' && <div className="frame-overlay">{tr(locale, 'Sin actualización · imagen no actual')}</div>}
        <div className="video-badges">
          <span className={`badge source-${v.source}`}>{tr(locale, v.source === 'recorded' ? 'Video pregrabado — Detecciones de YOLO' : v.sourceLabel)}</span>
          <span className="badge">
            <Dot tone={linkTone(v.link)} /> {linkLabel(v.link, locale)}
          </span>
        </div>
      </div>
      <div className="video-meta">
        <span>{tr(locale, v.source === 'recorded' ? 'Captura histórica' : 'Observación en vivo')}</span>
        {snap.recording && <span>{tr(locale, 'Fase de captura:')} <strong>{tr(locale, snap.recording.capturePhase === 'final_count' ? 'Verificación final' : snap.recording.capturePhase === 'start' ? 'Inicial' : 'Procedimiento')}</strong></span>}
        <span>{tr(locale, 'Procesamiento:')} <strong>{v.workerStatus ?? tr(locale, 'pendiente')}</strong></span>
        <span>{tr(locale, 'Actualizado:')} <strong>{v.lastUpdateAt ? clock(v.lastUpdateAt, locale) : '—'}</strong></span>
      </div>
    </section>
  );
}

function videoEmptyText(snap: MonitorSnapshot): string {
  if (snap.vision.link === 'unavailable') {
    return snap.vision.message === 'manual_mode'
      ? 'Sesión en modo manual: sin video.'
      : 'Video en vivo no disponible en el servidor.';
  }
  if (snap.vision.link === 'unauthorized') return 'Video sin autorización.';
  if (snap.vision.workerStatus === 'unbound') return 'VisionWorker no tiene esta sesión cargada.';
  if (snap.vision.link !== 'connected') return 'Conectando con VisionWorker…';
  if (snap.vision.workerStatus === 'processing' && snap.vision.source === 'recorded')
    return 'Procesando video pregrabado con YOLO. Esperando los primeros fotogramas.';
  return 'Esperando que el Operador inicie la captura en el portal.';
}

export function TrafficLight({ s, locale }: { s: SemaphoreView; locale: MonitorSnapshot['locale'] }) {
  return (
    <div className={`light light-${s.color}`} role="img" aria-label={`${locale === 'en' ? 'Traffic light' : 'Semáforo'} ${tr(locale, COLOR_NAME[s.color])}: ${tr(locale, s.title)}`} data-testid="semaphore" data-color={s.color} data-kind={s.kind}>
      {(['red', 'yellow', 'green'] as const).map((c) => (
        <span key={c} className={`lamp lamp-${c} ${s.color === c ? 'on' : ''}`} />
      ))}
      {s.color === 'gray' && <span className="lamp-gray-ring" />}
    </div>
  );
}

export function SidePanel({ snap }: { snap: MonitorSnapshot }) {
  const s = snap.semaphore;
  const locale = snap.locale;
  return (
    <aside className={`side side-${s.color}`} aria-live="polite">
      <TrafficLight s={s} locale={locale} />
      <div className="state-head">
        <span className={`state-icon tone-${s.color}`}><StateIcon icon={s.icon} size={46} /></span>
        <div>
          <div className="state-color">{tr(locale, COLOR_NAME[s.color])}</div>
          <div className="state-title" data-testid="semaphore-title">{tr(locale, s.title)}</div>
        </div>
      </div>
      {s.observed !== null ? (
        <div className="count" aria-label={tr(locale, 'Conteo observado sobre referencia')}>
          <span className="count-observed">{s.observed}</span>
          <span className="count-sep">/</span>
          <span className="count-ref">{s.reference ?? snap.reference?.total ?? '—'}</span>
        </div>
      ) : (
        <div className="count-none">
          {tr(locale, 'Referencia vigente:')} <strong>{snap.reference?.total ?? '—'}</strong>
          <div className="muted small">{tr(locale, 'Sin conteo observado confiable')}</div>
        </div>
      )}
      <div className="count-sub">
        {s.missing ? <span className="pill pill-missing">{tr(locale, `Faltan ${s.missing}`)}</span> : null}
        {s.surplus ? <span className="pill pill-surplus">{tr(locale, `Sobran ${s.surplus}`)}</span> : null}
        {!s.missing && !s.surplus && s.observed !== null ? <span className="pill">{tr(locale, 'Sin diferencia')}</span> : null}
      </div>
      <p className="state-msg">{tr(locale, s.message)}</p>
      <div className="kind-row">
        {s.kind === 'confirmed' && <span className="kind kind-confirmed">{tr(locale, 'CONFIRMADO OFICIALMENTE')}</span>}
        {s.kind === 'provisional' && <span className="kind kind-provisional">{tr(locale, 'LECTURA PROVISIONAL · NO AUTORIZA CIERRE')}</span>}
        {s.kind === 'none' && s.color !== 'gray' && <span className="kind kind-provisional">{tr(locale, 'LECTURA PROVISIONAL')}</span>}
        {s.stabilizing && <span className="kind kind-stab">{tr(locale, 'estabilizando…')}</span>}
      </div>
    </aside>
  );
}

export function StatusBar({ snap }: { snap: MonitorSnapshot }) {
  const locale = snap.locale;
  return (
    <footer className="statusbar">
      <span><Dot tone={backendTone(snap.connection.backend)} /> {tr(locale, 'Sistema:')} {backendLabel(snap.connection.backend, locale)}</span>
      <span><Dot tone={linkTone(snap.vision.link)} /> VisionWorker: {linkLabel(snap.vision.link, locale)}</span>
      <span>{tr(locale, 'Fuente:')} {tr(locale, snap.vision.sourceLabel)}</span>
      <span>{tr(locale, 'Última sincronización:')} {snap.connection.lastSyncAt ? clock(snap.connection.lastSyncAt, locale) : '—'}</span>
    </footer>
  );
}

export function MonitorScreen({ snap }: { snap: MonitorSnapshot }) {
  return (
    <main className="monitor">
      <div className="monitor-main">
        <VideoPanel snap={snap} />
        <SidePanel snap={snap} />
      </div>
      {snap.session && <section className="session-details" aria-label={snap.locale === 'en' ? 'Session details' : 'Datos de la sesión'}>
        <div><span>{tr(snap.locale, 'Paciente')}</span><strong>{snap.session.patientName || '—'}</strong></div>
        <div><span>{tr(snap.locale, 'Procedimiento')}</span><strong>{procedureText(snap.locale, snap.session.procedureCode, snap.session.procedureName)}</strong></div>
        <div><span>{tr(snap.locale, 'Doctor')}</span><strong>{snap.session.doctorName || '—'}</strong></div>
      </section>}
    </main>
  );
}
