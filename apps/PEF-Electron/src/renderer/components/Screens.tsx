import { useEffect, useState, type FormEvent } from 'react';
import type { MonitorSnapshot, PublicConfig } from '../../shared/types';
import { bridge } from '../bridge';
import { backendLabel, backendTone, clock } from '../format';
import { Dot, PefLogo, StateIcon } from './Icons';
import { translate as tr } from '../../shared/i18n';

function cameraText(snap: MonitorSnapshot): { tone: 'ok' | 'warn' | 'bad' | 'idle'; text: string } {
  const locale = snap.locale;
  const c = snap.camera;
  const local = c.localVideoInputs;
  const reg = c.registered;
  const parts: string[] = [];
  if (reg.length) parts.push(`${tr(locale, 'Registrada:')} ${reg.map((r) => `${r.name}${r.simulated ? ` (${tr(locale, 'simulada')})` : ''}`).join(', ')}`);
  else parts.push(tr(locale, 'Sin cámaras registradas en Administración'));
  if (local !== null) parts.push(tr(locale, local > 0 ? 'dispositivo de video detectado en este equipo' : 'ningún dispositivo de video en este equipo'));
  if (c.lastReported) parts.push(`${tr(locale, 'último reporte del portal:')} ${c.lastReported}${c.lastReportedAt ? ` (${clock(c.lastReportedAt, locale)})` : ''}`);
  const tone = reg.length && (local ?? 0) > 0 ? 'ok' : reg.length || (local ?? 0) > 0 ? 'warn' : 'bad';
  return { tone, text: parts.join(' · ') };
}

export function WaitingScreen({ snap }: { snap: MonitorSnapshot }) {
  const locale = snap.locale;
  const cam = cameraText(snap);
  const ambiguous = snap.appState === 'ambiguous';
  return (
    <main className="waiting" data-testid="waiting">
      <PefLogo size={96} />
      <h1>{snap.station?.name ?? tr(locale, 'Estación')}</h1>
      {snap.station?.roomName && <div className="muted big">{snap.station.roomName}</div>}
      <div className="waiting-light">
        <StateIcon icon={ambiguous ? 'alert' : 'pause'} size={54} />
        <div className="waiting-msg">{tr(locale, ambiguous ? 'Varias sesiones activas' : 'Esperando sesión del Operador')}</div>
      </div>
      {ambiguous && (
        <div className="notice warn">
          {tr(locale, snap.notice)}
          <ul>
            {snap.candidates.map((c) => (
              <li key={c.ref}>{tr(locale, 'Sesión')} {c.ref}{c.startedAt ? ` · ${tr(locale, 'iniciada')} ${clock(c.startedAt, locale)}` : ''}</li>
            ))}
          </ul>
        </div>
      )}
      <dl className="waiting-grid">
        <dt>{tr(locale, 'Conexión')}</dt>
        <dd><Dot tone={backendTone(snap.connection.backend)} /> {backendLabel(snap.connection.backend, locale)}</dd>
        <dt>{tr(locale, 'Cámara')}</dt>
        <dd><Dot tone={cam.tone} /> {cam.text}</dd>
        <dt>{tr(locale, 'Última sincronización:')}</dt>
        <dd>{snap.connection.lastSyncAt ? clock(snap.connection.lastSyncAt, locale) : '—'}</dd>
        {snap.user && (
          <>
            <dt>{tr(locale, 'Cuenta')}</dt>
            <dd>{snap.user.email}</dd>
          </>
        )}
      </dl>
      {snap.connection.backend !== 'ok' && snap.notice && !ambiguous && <div className="notice">{tr(locale, snap.notice)}</div>}
    </main>
  );
}

export function SetupScreen({ snap, config, onSaved }: { snap: MonitorSnapshot | null; config: PublicConfig | null; onSaved: () => void }) {
  const locale = snap?.locale ?? 'es-MX';
  const needsConfig = !config?.configured || snap?.appState === 'setup';
  const [apiBaseUrl, setApi] = useState(config?.apiBaseUrl || 'http://127.0.0.1:8080');
  const [authBaseUrl, setAuth] = useState(config?.authBaseUrl ?? '');
  const [sessionBaseUrl, setSession] = useState(config?.sessionBaseUrl ?? '');
  const [visionWsUrl, setWs] = useState(config?.visionWsUrl ?? '');
  const [stationId, setStation] = useState(config?.stationId ?? '');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(needsConfig);

  useEffect(() => setEditing(needsConfig), [needsConfig]);

  async function saveConfig(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await bridge().saveConfig({ apiBaseUrl, authBaseUrl, sessionBaseUrl, visionWsUrl, stationId });
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setEditing(false);
    onSaved();
  }

  async function login(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await bridge().login({ email, password });
    setPassword('');
    setBusy(false);
    if (!res.ok) return setError(res.error);
    onSaved();
  }

  return (
    <main className="setup" data-testid="setup">
      <PefLogo size={72} />
      <h1>{tr(locale, 'Configuración de la estación')}</h1>
      <p className="muted">
        {tr(locale, 'Este equipo muestra el monitoreo de la sesión que el Operador inicia en el portal. No crea, valida ni cierra sesiones.')}
      </p>
      {snap?.notice && <div className="notice warn">{tr(locale, snap.notice)}</div>}
      {editing ? (
        <form className="card form" onSubmit={saveConfig}>
          <label>
            {tr(locale, 'URL del API Gateway (o de los servicios)')}
            <input value={apiBaseUrl} onChange={(e) => setApi(e.target.value)} placeholder="https://api.hospital.example" required />
          </label>
          <label>
            {tr(locale, 'Identificador de la estación de conteo (UUID)')}
            <input value={stationId} onChange={(e) => setStation(e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required />
          </label>
          <details>
            <summary>{tr(locale, 'Opciones avanzadas (sin gateway)')}</summary>
            <label>
              AuthService
              <input value={authBaseUrl} onChange={(e) => setAuth(e.target.value)} placeholder="http://127.0.0.1:5001" />
            </label>
            <label>
              SessionService
              <input value={sessionBaseUrl} onChange={(e) => setSession(e.target.value)} placeholder="http://127.0.0.1:5004" />
            </label>
            <label>
              {tr(locale, 'WebSocket de VisionWorker')}
              <input value={visionWsUrl} onChange={(e) => setWs(e.target.value)} placeholder="ws://127.0.0.1:5002/ws" />
            </label>
          </details>
          <button className="btn primary" disabled={busy}>{tr(locale, 'Guardar configuración')}</button>
        </form>
      ) : (
        <form className="card form" onSubmit={login}>
          <div className="muted small">
            {tr(locale, 'Estación')} {config?.stationId} · {config?.apiBaseUrl}{' '}
            <button type="button" className="link" onClick={() => setEditing(true)}>{tr(locale, 'cambiar')}</button>
          </div>
          <label>
            {tr(locale, 'Correo del Operador')}
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            {tr(locale, 'Contraseña')}
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <button className="btn primary" disabled={busy}>{tr(locale, 'Iniciar sesión')}</button>
          <p className="muted small">
            {config?.secureStorage
              ? tr(locale, 'La contraseña no se guarda. El token de renovación se cifra con el almacén seguro de Windows.')
              : tr(locale, 'Almacenamiento seguro no disponible: deberá iniciar sesión cada vez que abra la aplicación.')}
          </p>
        </form>
      )}
      {error && <div className="notice bad" role="alert">{tr(locale, error)}</div>}
    </main>
  );
}

export function ErrorScreen({ snap, onSetup }: { snap: MonitorSnapshot; onSetup: () => void }) {
  return (
    <main className="waiting">
      <StateIcon icon="stop" size={72} />
      <h1>{tr(snap.locale, 'No se puede monitorear esta estación')}</h1>
      <div className="notice bad">{tr(snap.locale, snap.notice)}</div>
      <div className="row">
        <button className="btn" onClick={() => void bridge().retry()}>{tr(snap.locale, 'Reintentar')}</button>
        <button className="btn" onClick={onSetup}>{tr(snap.locale, 'Configuración')}</button>
      </div>
    </main>
  );
}

export function SettingsDrawer({ snap, config, onClose, onSetup, onChanged }: {
  snap: MonitorSnapshot;
  config: PublicConfig | null;
  onClose: () => void;
  onSetup: () => void;
  onChanged: () => void;
}) {
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" role="dialog" aria-label={tr(snap.locale, 'Opciones')} onClick={(e) => e.stopPropagation()}>
        <h2>{tr(snap.locale, 'Opciones')}</h2>
        <dl className="waiting-grid">
          <dt>{tr(snap.locale, 'Estación')}</dt>
          <dd>{snap.station?.name ?? config?.stationId ?? '—'}</dd>
          <dt>{tr(snap.locale, 'Servidor')}</dt>
          <dd>{config?.apiBaseUrl || '—'}</dd>
          <dt>{tr(snap.locale, 'Cuenta')}</dt>
          <dd>{snap.user?.email ?? '—'}</dd>
        </dl>
        <label className="check">
          <input
            type="checkbox"
            checked={snap.autostart}
            disabled={snap.demo}
            onChange={async (e) => {
              await bridge().setAutostart(e.target.checked);
              onChanged();
            }}
          />
          {tr(snap.locale, 'Iniciar con Windows (minimizado en la bandeja)')}
        </label>
        <div className="col">
          <button className="btn" onClick={() => void bridge().hideToTray()}>{tr(snap.locale, 'Minimizar a la bandeja')}</button>
          <button className="btn" onClick={() => void bridge().retry()}>{tr(snap.locale, 'Sincronizar ahora')}</button>
          {!snap.demo && <button className="btn" onClick={onSetup}>{tr(snap.locale, 'Configuración de la estación')}</button>}
          {!snap.demo && (
            <button
              className="btn danger"
              onClick={async () => {
                await bridge().logout();
                onChanged();
                onClose();
              }}
            >
              {tr(snap.locale, 'Cerrar sesión de la estación')}
            </button>
          )}
          <button className="btn" onClick={onClose}>{tr(snap.locale, 'Cerrar')}</button>
        </div>
      </div>
    </div>
  );
}
