// Windows "start with the user" (HKCU\Software\Microsoft\Windows\CurrentVersion\Run via app.setLoginItemSettings).
// The process starts hidden in the tray and only shows the window when a session of its station is active.
// A server can never launch this app on another PC: it must already be running (autostart), or be opened by the
// user / a pef-monitor://open link on the same PC.
import * as path from 'node:path';

export function autostartSettings(enabled: boolean, isPackaged: boolean, execPath: string, appPath: string) {
  return {
    openAtLogin: enabled,
    path: execPath,
    // Unpackaged (development) Electron needs the app folder as first argument.
    args: isPackaged ? ['--hidden'] : [path.resolve(appPath), '--hidden'],
  };
}

export function isOpenRequest(args: string[]): boolean {
  // Only the exact "open" action is honoured; nothing from the URL (ids, hosts) is ever trusted or used.
  return args.some((a) => /^pef-monitor:\/\/open\/?$/i.test(a));
}
