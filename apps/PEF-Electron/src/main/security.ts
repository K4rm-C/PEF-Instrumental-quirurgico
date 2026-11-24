// Electron hardening (https://www.electronjs.org/docs/latest/tutorial/security):
// - renderer served from a privileged custom protocol (pef-app://monitor/) with a strict CSP header;
// - path traversal impossible: only files under dist/renderer are served;
// - no navigation, no new windows, no <webview>, no permissions (camera included: the monitor never opens it);
// - TLS errors are never overridden.
import * as fs from 'node:fs';
import * as path from 'node:path';
import { app, protocol, session, type WebContents } from 'electron';
import { APP_ORIGIN } from '../shared/ipc';

export const APP_SCHEME = 'pef-app';
export const APP_ENTRY = `${APP_ORIGIN}/index.html`;

export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'none'",
  "media-src 'self'",
  "object-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

/** Must run before app 'ready'. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: false, corsEnabled: false, stream: true } },
  ]);
}

/** Resolves a pef-app:// URL to a file inside `root`, or null (traversal, other host, unknown type). */
export function resolveAppFile(root: string, requestUrl: string): string | null {
  let url: URL;
  try {
    url = new URL(requestUrl);
  } catch {
    return null;
  }
  if (url.protocol !== `${APP_SCHEME}:` || url.host !== 'monitor') return null;
  let rel: string;
  try {
    rel = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (rel === '/' || rel === '') rel = '/index.html';
  const full = path.resolve(root, `.${rel}`);
  const rootResolved = path.resolve(root);
  if (full !== rootResolved && !full.startsWith(rootResolved + path.sep)) return null;
  if (!MIME[path.extname(full).toLowerCase()]) return null;
  return full;
}

export function serveRenderer(rendererRoot: string, recording?: (runId: string, request: Request) => Promise<Response>): void {
  protocol.handle(APP_SCHEME, async (request) => {
    const url = new URL(request.url);
    const run = /^\/recording\/([0-9a-f]{12})$/.exec(url.pathname);
    if (url.host === 'monitor' && run) return recording ? recording(run[1], request) : new Response('Unavailable', { status: 404 });
    const file = resolveAppFile(rendererRoot, request.url);
    if (!file || !fs.existsSync(file)) return new Response('Not found', { status: 404 });
    const body = await fs.promises.readFile(file);
    return new Response(body, {
      status: 200,
      headers: {
        'Content-Type': MIME[path.extname(file).toLowerCase()],
        'Content-Security-Policy': CSP,
        'X-Content-Type-Options': 'nosniff',
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Referrer-Policy': 'no-referrer',
      },
    });
  });
}

export function isAppUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === `${APP_SCHEME}:` && u.host === 'monitor';
  } catch {
    return false;
  }
}

export function hardenSession(log: (m: string) => void): void {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, permission, callback) => {
    log(`permission denied: ${permission}`);
    callback(false);
  });
  ses.setPermissionCheckHandler(() => false);
  // Never download anything.
  ses.on('will-download', (event) => event.preventDefault());
}

export function hardenWebContents(log: (m: string) => void): void {
  app.on('web-contents-created', (_event, contents: WebContents) => {
    contents.setWindowOpenHandler(({ url }) => {
      log(`blocked window.open ${url.slice(0, 80)}`);
      return { action: 'deny' };
    });
    contents.on('will-navigate', (event, url) => {
      if (!isAppUrl(url)) {
        event.preventDefault();
        log(`blocked navigation ${url.slice(0, 80)}`);
      }
    });
    contents.on('will-redirect', (event, url) => {
      if (!isAppUrl(url)) event.preventDefault();
    });
    contents.on('will-attach-webview', (event) => event.preventDefault());
  });
  // Certificate errors are rejected (default behaviour, made explicit and logged).
  app.on('certificate-error', (event, _wc, url, error, _cert, callback) => {
    event.preventDefault();
    log(`TLS certificate rejected for ${new URL(url).host}: ${error}`);
    callback(false);
  });
}
