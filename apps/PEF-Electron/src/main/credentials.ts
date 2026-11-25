// Refresh token at rest, encrypted with the OS key store (Windows: DPAPI, bound to the Windows user) through
// Electron safeStorage. If encryption is not available the token is kept in memory only (never in clear text on
// disk) and the Operator signs in again after a restart. Passwords are never stored.
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { SecretStore } from './api';

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plain: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

export class EncryptedFileSecretStore implements SecretStore {
  private cache: string | null | undefined;
  private memoryOnly: string | null = null;
  private readonly file: string;

  constructor(
    dir: string,
    private readonly safe: SafeStorageLike,
    private readonly onWarn: (m: string) => void = () => undefined,
  ) {
    this.file = path.join(dir, 'credentials.bin');
  }

  get persistent(): boolean {
    return this.safe.isEncryptionAvailable();
  }

  get(): string | null {
    if (!this.persistent) return this.memoryOnly;
    if (this.cache !== undefined) return this.cache;
    try {
      this.cache = this.safe.decryptString(fs.readFileSync(this.file));
    } catch {
      this.cache = null;
    }
    return this.cache;
  }

  set(value: string): void {
    if (!this.persistent) {
      this.onWarn('secure storage unavailable: refresh token kept in memory only');
      this.memoryOnly = value;
      return;
    }
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, this.safe.encryptString(value), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
    this.cache = value;
  }

  clear(): void {
    this.cache = null;
    this.memoryOnly = null;
    try {
      fs.rmSync(this.file, { force: true });
    } catch {
      /* ignore */
    }
  }
}
