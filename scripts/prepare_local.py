#!/usr/bin/env python3
"""Prepara archivos privados para desarrollo local (sin dependencias externas)."""
from __future__ import annotations
import argparse
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env'
GARAGE = ROOT / 'infra' / 'garage.toml'


def prepare() -> None:
    if ENV.exists() or GARAGE.exists():
        existing = ', '.join(str(x.relative_to(ROOT)) for x in (ENV, GARAGE) if x.exists())
        raise SystemExit(f'Ya existen archivos locales: {existing}. No se sobrescribieron para preservar acceso a datos. Si necesitas recrearlos, renombra ambos antes de repetir.')
    template = (ROOT / '.env.example').read_text(encoding='utf-8')
    replacements = {
        'CAMBIAR_PASSWORD_POSTGRES': secrets.token_hex(24),
        'CAMBIAR_PASSWORD_REDIS': secrets.token_hex(24),
        'CAMBIAR_PASSWORD_MONGO': secrets.token_hex(24),
        'CAMBIAR_ACCESS_KEY_GARAGE': 'GK' + secrets.token_hex(12),
        'CAMBIAR_SECRET_KEY_GARAGE': secrets.token_hex(32),
        'CAMBIAR_CLAVE_JWT_LARGA_Y_ALEATORIA': secrets.token_hex(32),
        'CAMBIAR_CLAVE_FLASK_LARGA_Y_ALEATORIA': secrets.token_hex(32),
    }
    for marker, value in replacements.items():
        if marker not in template:
            raise SystemExit(f'No se encontro variable requerida: {marker}')
        template = template.replace(marker,value)
    garage_template = (ROOT / 'infra' / 'garage.toml.example').read_text(encoding='utf-8')
    for marker, value in {
        'REEMPLAZAR_CON_64_CARACTERES_HEXADECIMALES':secrets.token_hex(32),
        'REEMPLAZAR_TOKEN_ADMIN':secrets.token_hex(32),
        'REEMPLAZAR_TOKEN_METRICAS':secrets.token_hex(32),
    }.items():
        if marker not in garage_template:
            raise SystemExit(f'No se encontro variable requerida: {marker}')
        garage_template = garage_template.replace(marker,value)
    (ROOT / 'apps' / 'VisionWorker' / 'weights').mkdir(parents=True,exist_ok=True)
    ENV.write_text(template,encoding='utf-8')
    try:
        GARAGE.write_text(garage_template,encoding='utf-8')
    except Exception:
        ENV.unlink(missing_ok=True)
        raise
    try:
        ENV.chmod(0o600)
        GARAGE.chmod(0o600)
    except OSError:
        pass
    print('Configuracion local creada: .env e infra/garage.toml (ignorados por git).')
    print('Para usar conteo real: copia best.pt a apps/VisionWorker/weights/best.pt.')
    print('Ejecuta: docker compose up -d --build')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    prepare()
