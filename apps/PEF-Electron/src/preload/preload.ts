// Minimal, sandboxed bridge. The renderer gets exactly these functions and nothing else: no ipcRenderer,
// no Node, no file system, no tokens. Channel names are literal on purpose (a sandboxed preload cannot load
// local modules); tests/ipc.test.ts checks they match src/shared/ipc.ts.
import { contextBridge, ipcRenderer } from 'electron';

const CH = {
  getSnapshot: 'pef:get-snapshot',
  getConfig: 'pef:get-config',
  saveConfig: 'pef:save-config',
  login: 'pef:login',
  logout: 'pef:logout',
  setAutostart: 'pef:set-autostart',
  hideToTray: 'pef:hide-to-tray',
  reportLocalCameras: 'pef:report-local-cameras',
  retry: 'pef:retry',
  snapshotEvent: 'pef:snapshot',
} as const;

const api = {
  getSnapshot: () => ipcRenderer.invoke(CH.getSnapshot),
  getConfig: () => ipcRenderer.invoke(CH.getConfig),
  saveConfig: (cfg: unknown) => ipcRenderer.invoke(CH.saveConfig, cfg),
  login: (credentials: unknown) => ipcRenderer.invoke(CH.login, credentials),
  logout: () => ipcRenderer.invoke(CH.logout),
  setAutostart: (enabled: boolean) => ipcRenderer.invoke(CH.setAutostart, enabled),
  hideToTray: () => ipcRenderer.invoke(CH.hideToTray),
  reportLocalCameras: (report: unknown) => ipcRenderer.invoke(CH.reportLocalCameras, report),
  retry: () => ipcRenderer.invoke(CH.retry),
  onSnapshot: (listener: (snapshot: unknown) => void) => {
    const wrapped = (_event: unknown, snapshot: unknown) => listener(snapshot);
    ipcRenderer.on(CH.snapshotEvent, wrapped);
    return () => {
      ipcRenderer.removeListener(CH.snapshotEvent, wrapped);
    };
  },
};

contextBridge.exposeInMainWorld('pef', Object.freeze(api));
