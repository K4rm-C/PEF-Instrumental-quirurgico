// Diagnostic log without secrets: userData/logs/monitor.log (rotated, 3 x 1 MB).
// Every line is passed through redact(): JWTs, monitor tickets, bearer headers, passwords, refresh tokens and
// base64 images never reach the file.
import * as fs from 'node:fs';
import * as path from 'node:path';

const MAX_BYTES = 1024 * 1024;
const KEEP = 3;

export function redact(text: string): string {
  return text
    .replace(/eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g, '[jwt]')
    .replace(/\bm1\.[0-9a-f-]{36}\.\d+\.[0-9a-f]+\.[0-9a-f]{64}\b/gi, '[monitor-ticket]')
    .replace(/(bearer\s+)[^\s"']+/gi, '$1[redacted]')
    .replace(/("?(?:password|refresh_token|access_token|auth_token|ticket|jpeg_b64)"?\s*[:=]\s*)"[^"]*"/gi, '$1"[redacted]"')
    .replace(/[A-Za-z0-9+/]{200,}={0,2}/g, '[base64]');
}

export class Logger {
  private readonly file: string | null;

  constructor(dir: string | null, private readonly echo = false) {
    this.file = dir ? path.join(dir, 'monitor.log') : null;
    if (dir) {
      try {
        fs.mkdirSync(dir, { recursive: true });
      } catch {
        /* logging must never break the app */
      }
    }
  }

  private rotate(): void {
    if (!this.file) return;
    try {
      if (fs.statSync(this.file).size < MAX_BYTES) return;
    } catch {
      return;
    }
    for (let i = KEEP - 1; i >= 1; i--) {
      try {
        fs.renameSync(`${this.file}.${i}`, `${this.file}.${i + 1}`);
      } catch {
        /* missing generation */
      }
    }
    try {
      fs.renameSync(this.file, `${this.file}.1`);
    } catch {
      /* ignore */
    }
  }

  write(level: 'INFO' | 'WARN' | 'ERROR', message: string, extra?: unknown): void {
    let line = `${new Date().toISOString()} ${level} ${message}`;
    if (extra !== undefined) {
      try {
        line += ` ${extra instanceof Error ? `${extra.name}: ${extra.message}` : JSON.stringify(extra)}`;
      } catch {
        line += ' [unserializable]';
      }
    }
    line = redact(line);
    if (this.echo) console.log(line);
    if (!this.file) return;
    try {
      this.rotate();
      fs.appendFileSync(this.file, `${line}\n`, 'utf8');
    } catch {
      /* ignore */
    }
  }

  info(message: string, extra?: unknown): void {
    this.write('INFO', message, extra);
  }

  warn(message: string, extra?: unknown): void {
    this.write('WARN', message, extra);
  }

  error(message: string, extra?: unknown): void {
    this.write('ERROR', message, extra);
  }
}
