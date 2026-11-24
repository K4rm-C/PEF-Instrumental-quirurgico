import type { LightIcon } from '../../shared/types';

export function PefLogo({ size = 40 }: { size?: number }) {
  return (
    <svg className="pef-logo" width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="PEF">
      <rect x="2" y="2" width="44" height="44" rx="12" fill="var(--primary)" />
      <path d="M14 34V14h9a6 6 0 0 1 0 12h-9" fill="none" stroke="var(--on-primary)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="34" cy="31" r="4" fill="var(--on-primary)" />
    </svg>
  );
}

export function StateIcon({ icon, size = 44 }: { icon: LightIcon; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };
  switch (icon) {
    case 'check':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="m7.5 12.5 3 3 6-6.5" />
        </svg>
      );
    case 'alert':
      return (
        <svg {...common}>
          <path d="M10.3 3.9 1.8 18.5A2 2 0 0 0 3.5 21.5h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          <path d="M12 9v4.5M12 17.2v.1" />
        </svg>
      );
    case 'stop':
      return (
        <svg {...common}>
          <path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z" />
          <path d="M12 7v6M12 16.6v.1" />
        </svg>
      );
    case 'pause':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M10 8.5v7M14 8.5v7" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M9.4 9a2.7 2.7 0 0 1 5.2 1c0 1.8-2.6 2.5-2.6 2.5M12 16.8v.1" />
        </svg>
      );
  }
}

export function Dot({ tone }: { tone: 'ok' | 'warn' | 'bad' | 'idle' }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}
