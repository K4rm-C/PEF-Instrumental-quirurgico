import type { MonitorSnapshot } from '../shared/types';
import { translate } from '../shared/i18n';

export function clock(iso: string, locale: MonitorSnapshot['locale'] = 'es-MX'): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString(locale === 'en' ? 'en-US' : 'es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function ageText(seconds: number): string {
  if (seconds < 1) return 'hace <1 s';
  if (seconds < 90) return `hace ${Math.round(seconds)} s`;
  return `hace ${Math.round(seconds / 60)} min`;
}

export function backendLabel(b: MonitorSnapshot['connection']['backend'], locale: MonitorSnapshot['locale'] = 'es-MX'): string {
  return translate(locale, { ok: 'Conectado', reconnecting: 'Reconectando', down: 'Sin conexión', unauthorized: 'Sin autorización', unknown: 'Verificando' }[b]);
}

export function backendTone(b: MonitorSnapshot['connection']['backend']): 'ok' | 'warn' | 'bad' | 'idle' {
  return b === 'ok' ? 'ok' : b === 'reconnecting' ? 'warn' : b === 'unknown' ? 'idle' : 'bad';
}

export function linkLabel(l: MonitorSnapshot['vision']['link'], locale: MonitorSnapshot['locale'] = 'es-MX'): string {
  return translate(locale, {
    idle: 'Inactivo',
    connecting: 'Conectando',
    connected: 'Conectado',
    reconnecting: 'Reconectando',
    unavailable: 'No disponible',
    unauthorized: 'Sin autorización',
  }[l]);
}

export function linkTone(l: MonitorSnapshot['vision']['link']): 'ok' | 'warn' | 'bad' | 'idle' {
  return l === 'connected' ? 'ok' : l === 'connecting' || l === 'reconnecting' ? 'warn' : l === 'idle' ? 'idle' : 'bad';
}
