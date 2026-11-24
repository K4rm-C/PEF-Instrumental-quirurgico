// Typed access to the preload bridge (window.pef). The renderer has no other way out.
import type { IpcResult, SaveConfigInput } from '../shared/ipc';
import type { MonitorSnapshot, PublicConfig } from '../shared/types';

export interface PefBridge {
  getSnapshot(): Promise<IpcResult<MonitorSnapshot | null>>;
  getConfig(): Promise<IpcResult<PublicConfig>>;
  saveConfig(cfg: SaveConfigInput): Promise<IpcResult<PublicConfig>>;
  login(credentials: { email: string; password: string }): Promise<IpcResult<{ email: string }>>;
  logout(): Promise<IpcResult<null>>;
  setAutostart(enabled: boolean): Promise<IpcResult<PublicConfig>>;
  hideToTray(): Promise<IpcResult<null>>;
  reportLocalCameras(report: { videoInputs: number }): Promise<IpcResult<null>>;
  retry(): Promise<IpcResult<null>>;
  onSnapshot(listener: (snapshot: MonitorSnapshot) => void): () => void;
}

declare global {
  interface Window {
    pef?: PefBridge;
  }
}

export function bridge(): PefBridge {
  if (!window.pef) throw new Error('PEF bridge unavailable');
  return window.pef;
}
