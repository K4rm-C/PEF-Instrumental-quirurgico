import { useCallback, useEffect, useState } from 'react';
import type { MonitorSnapshot, PublicConfig } from '../shared/types';
import { bridge } from './bridge';
import { Header, MonitorScreen, StatusBar } from './components/Monitor';
import { ErrorScreen, SettingsDrawer, SetupScreen, WaitingScreen } from './components/Screens';
import { PefLogo } from './components/Icons';
import { translate as tr } from '../shared/i18n';

/** Counts local video inputs WITHOUT opening any camera (the portal owns it). */
async function reportLocalCameras(): Promise<void> {
  try {
    const devices = await navigator.mediaDevices?.enumerateDevices?.();
    if (!devices) return;
    await bridge().reportLocalCameras({ videoInputs: Math.min(32, devices.filter((d) => d.kind === 'videoinput').length) });
  } catch {
    /* informative only */
  }
}

export function App() {
  const [snap, setSnap] = useState<MonitorSnapshot | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [menu, setMenu] = useState(false);
  const [forceSetup, setForceSetup] = useState(false);

  useEffect(() => {
    document.documentElement.lang = snap?.locale ?? 'es-MX';
  }, [snap?.locale]);

  const loadConfig = useCallback(async () => {
    const res = await bridge().getConfig();
    if (res.ok && res.value) setConfig(res.value);
  }, []);

  useEffect(() => {
    void loadConfig();
    void bridge().getSnapshot().then((r) => r.ok && r.value && setSnap(r.value));
    const off = bridge().onSnapshot((s) => setSnap(s));
    void reportLocalCameras();
    const timer = setInterval(() => void reportLocalCameras(), 30000);
    navigator.mediaDevices?.addEventListener?.('devicechange', reportLocalCameras);
    return () => {
      off();
      clearInterval(timer);
      navigator.mediaDevices?.removeEventListener?.('devicechange', reportLocalCameras);
    };
  }, [loadConfig]);

  const appState = snap?.appState ?? 'connecting';
  const color = snap?.semaphore.color ?? 'gray';
  const showBanner = Boolean(snap?.notice && snap.connection.backend === 'reconnecting' &&
    (appState === 'monitoring' || appState === 'review'));

  let body;
  if (!snap) {
    body = (
      <main className="waiting">
        <PefLogo size={96} />
        <div className="waiting-msg">{tr('es-MX', 'Iniciando…')}</div>
      </main>
    );
  } else if (forceSetup || appState === 'setup' || appState === 'login_required') {
    body = (
      <SetupScreen
        key={config ? `${config.stationId}|${config.apiBaseUrl}` : 'loading'}
        snap={snap}
        config={config}
        onSaved={() => {
          setForceSetup(false);
          void loadConfig();
        }}
      />
    );
  } else if (appState === 'error') {
    body = <ErrorScreen snap={snap} onSetup={() => setForceSetup(true)} />;
  } else if (appState === 'monitoring' || appState === 'review' || appState === 'ended') {
    body = <MonitorScreen snap={snap} />;
  } else {
    body = <WaitingScreen snap={snap} />;
  }

  return (
    <div className={`app tone-bg-${color}${showBanner ? ' with-banner' : ''}`} lang={snap?.locale ?? 'es-MX'} data-testid="app-root" data-app-state={appState}>
      {snap && <Header snap={snap} onMenu={() => setMenu(true)} />}
      {showBanner && snap?.notice && (
        <div className="banner warn" role="status">{tr(snap.locale, snap.notice)}</div>
      )}
      {body}
      {snap && (appState === 'monitoring' || appState === 'review' || appState === 'ended') && <StatusBar snap={snap} />}
      {menu && snap && (
        <SettingsDrawer
          snap={snap}
          config={config}
          onClose={() => setMenu(false)}
          onSetup={() => {
            setMenu(false);
            setForceSetup(true);
          }}
          onChanged={() => void loadConfig()}
        />
      )}
    </div>
  );
}
