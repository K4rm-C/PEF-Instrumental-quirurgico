"""MS-12: generates the four EDITABLE draw.io diagrams of docs/diagramas/ (mxGraph XML, open them in diagrams.net).

    python scripts/make_ms12_diagrams.py

1. ms12_contenedores.drawio      containers: gateway, portal, the 9 services, workers, VisionWorker, data stores.
2. ms12_dominios.drawio          the 20 functional domains (professor's port 53xx) INSIDE the process that hosts them
                                  (a shared-process domain is never drawn as its own container).
3. ms12_flujo_conteo.drawio      Supervisor -> Operator -> VisionWorker -> EvidenceService flow of a counting session.
4. ms12_despliegue_compose.drawio Docker Compose deployment: services, network, volumes, published ports, profiles.

The files are generated (reproducible) and can then be edited by hand in draw.io.
"""
from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'docs' / 'diagramas'

STYLE = {
    'process': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=12;',
    'portal': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=12;',
    'worker': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=11;',
    'vision': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;fontSize=12;',
    'store': 'shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;size=10;fillColor=#f5f5f5;strokeColor=#666666;fontSize=11;',
    'gateway': 'rounded=1;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;fontSize=12;fontStyle=1;',
    'client': 'shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;fontSize=11;',
    'future': 'rounded=1;whiteSpace=wrap;html=1;dashed=1;fillColor=#ffffff;strokeColor=#999999;fontColor=#777777;fontSize=11;',
    'test': 'rounded=1;whiteSpace=wrap;html=1;dashed=1;fillColor=#f8cecc;strokeColor=#b85450;fontSize=11;',
    'group': 'rounded=1;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#6c8ebf;dashed=0;verticalAlign=top;align=left;'
             'spacingLeft=8;fontStyle=1;fontSize=12;container=1;',
    'groupw': 'rounded=1;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#82b366;verticalAlign=top;align=left;spacingLeft=8;'
              'fontStyle=1;fontSize=12;container=1;',
    'groupv': 'rounded=1;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#d79b00;verticalAlign=top;align=left;spacingLeft=8;'
              'fontStyle=1;fontSize=12;container=1;',
    'domain': 'rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#333333;fontSize=10;align=left;spacingLeft=4;',
    'note': 'text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;fontSize=11;',
    'title': 'text;html=1;align=left;verticalAlign=middle;fontSize=16;fontStyle=1;',
    'edge': 'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;fontSize=10;',
    'edge_dash': 'edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;dashed=1;fontSize=10;',
    'edge_straight': 'html=1;endArrow=block;fontSize=10;',
}


def _value(label: str) -> str:
    """draw.io HTML label: escape the text, line breaks as <br>, then XML-escape the attribute."""
    return html.escape(html.escape(label).replace(chr(10), '<br>'))


class Diagram:
    def __init__(self, name: str, width: int = 1800, height: int = 1200):
        self.name, self.width, self.height = name, width, height
        self.cells: list[str] = []
        self.ids = 1

    def _id(self) -> str:
        self.ids += 1
        return f'c{self.ids}'

    def box(self, label: str, x: int, y: int, w: int, h: int, style: str = 'process', parent: str = '1') -> str:
        cid = self._id()
        self.cells.append(f'<mxCell id="{cid}" value="{_value(label)}" style="{STYLE.get(style, style)}" vertex="1" '
                          f'parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def edge(self, source: str, target: str, label: str = '', style: str = 'edge') -> str:
        cid = self._id()
        self.cells.append(f'<mxCell id="{cid}" value="{_value(label)}" style="{STYLE.get(style, style)}" edge="1" parent="1" '
                          f'source="{source}" target="{target}"><mxGeometry relative="1" as="geometry"/></mxCell>')
        return cid

    def save(self) -> Path:
        OUT.mkdir(parents=True, exist_ok=True)
        body = ''.join(self.cells)
        xml = (f'<mxfile host="PEF-MS12" type="device"><diagram id="{self.name}" name="{self.name}">'
               f'<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
               f'fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0">'
               f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram></mxfile>\n')
        path = OUT / f'{self.name}.drawio'
        path.write_text(xml, encoding='utf-8')
        return path


SERVICES = [  # name, port, short responsibility
    ('AuthService', 5001, 'JWT, refresh, revocación (Redis), clientes máquina'),
    ('VisionWorker', 5002, 'YOLO, tracking, /ws tiempo real'),
    ('CatalogService', 5003, 'familias, kits, charolas, equivalencias'),
    ('SessionService', 5004, 'sesiones (Supervisor), discrepancias, reportes, tracking, sello'),
    ('EvidenceService', 5005, 'imágenes en Garage, URL firmadas'),
    ('AuditService', 5006, 'consulta de access_audit'),
    ('NotificationsService', 5007, 'bandeja, outbox'),
    ('MonitoringService', 5008, 'salud, estaciones, cámaras'),
    ('IntegrationService', 5009, 'sync, FHIR R4, entregas, lotes'),
]


def containers() -> Path:
    d = Diagram('ms12_contenedores', 1900, 1250)
    d.box('PEF — Diagrama de contenedores (MS-12, Docker Compose)', 20, 10, 900, 30, 'title')
    users = [d.box(role, 40 + i * 110, 70, 30, 60, 'client') for i, role in enumerate(('Administrador', 'Operador', 'Supervisor'))]
    desk = d.box('App de escritorio (estación)\nNO EXISTE: solo API documentada', 420, 70, 220, 60, 'future')
    andr = d.box('App Android\nNO EXISTE: solo API documentada', 660, 70, 200, 60, 'future')
    machine = d.box('Cliente máquina autorizado\n(client_credentials)', 880, 70, 200, 60, 'future')
    gw = d.box('API Gateway nginx 1.27 (TLS 443)\nportal.<dominio> / api.<dominio>\nrutas publicadas = contratos', 300, 200, 560, 70, 'gateway')
    for u in users:
        d.edge(u, gw, 'HTTPS')
    for c in (desk, andr, machine):
        d.edge(c, gw, 'HTTPS (API)', 'edge_dash')
    web = d.box('BackendWebFlask :5000\nAdministración (monolito) + Operador (monolito)\n+ pantallas del Supervisor (cliente de microservicios)\noutbox de evidencias/checkpoints',
                40, 330, 380, 100, 'portal')
    d.edge(gw, web, 'portal')
    y = 330
    boxes = {}
    for i, (name, port, text) in enumerate(SERVICES):
        col, row = i % 3, i // 3
        style = 'vision' if name == 'VisionWorker' else 'process'
        boxes[name] = d.box(f'{name} :{port}\n{text}', 470 + col * 300, y + row * 110, 280, 80, style)
    d.edge(gw, boxes['AuthService'], 'api')
    d.edge(web, boxes['VisionWorker'], 'bind firmado / estado')
    d.edge(web, boxes['SessionService'], 'Supervisor (JWT)')
    d.edge(web, boxes['EvidenceService'], 'outbox: JPEG')
    workers = {
        'notifications-worker': d.box('notifications-worker\n(outbox → bandeja / correo)', 470, 680, 220, 55, 'worker'),
        'monitoring-collector': d.box('monitoring-collector\n(ciclo de sondas, transiciones)', 710, 680, 220, 55, 'worker'),
        'integration-worker': d.box('integration-worker\n(entregas FHIR con reintentos)', 950, 680, 220, 55, 'worker'),
        'db-migrate': d.box('db-migrate (una vez)\n004..019 en una transacción', 1190, 680, 220, 55, 'worker'),
    }
    d.edge(boxes['NotificationsService'], workers['notifications-worker'], '', 'edge_dash')
    d.edge(boxes['MonitoringService'], workers['monitoring-collector'], '', 'edge_dash')
    d.edge(boxes['IntegrationService'], workers['integration-worker'], '', 'edge_dash')
    stores = {
        'pg': d.box('PostgreSQL 16\nfuente de verdad (DS04)', 300, 820, 200, 80, 'store'),
        'redis': d.box('Redis 7\nrevocación JWT, caché', 540, 820, 180, 80, 'store'),
        'mongo': d.box('MongoDB 7\ncheckpoints (DS06)', 760, 820, 180, 80, 'store'),
        'garage': d.box('Garage 2.4 (S3)\nimágenes y pesos', 980, 820, 180, 80, 'store'),
    }
    for name in ('AuthService', 'CatalogService', 'SessionService', 'AuditService', 'NotificationsService',
                 'MonitoringService', 'IntegrationService', 'EvidenceService'):
        d.edge(boxes[name], stores['pg'], '', 'edge_dash')
    d.edge(web, stores['pg'], 'SQL (Admin/Operador)')
    d.edge(boxes['AuthService'], stores['redis'], 'auth:jwt:*', 'edge_dash')
    d.edge(web, stores['mongo'], 'checkpoints', 'edge_dash')
    d.edge(boxes['EvidenceService'], stores['garage'], 'S3', 'edge_dash')
    d.edge(boxes['VisionWorker'], stores['garage'], 'pesos (SHA-256)', 'edge_dash')
    hapi = d.box('HAPI FHIR R4 (SOLO PRUEBAS)\nno es un hospital real', 1200, 560, 230, 60, 'test')
    d.edge(workers['integration-worker'], hapi, 'Bundle transaction', 'edge_dash')
    d.box('Notas:\n• 9 servicios de negocio (5001–5009) + portal 5000; los 20 dominios del profesor se distribuyen en estos procesos '
          '(ver ms12_dominios.drawio).\n• Recuadros punteados grises: clientes que todavía NO existen.\n• Recuadro rojo punteado: '
          'dependencia de pruebas, no de producción.\n• Solo el Gateway publica puertos en producción (MS-04).',
          1200, 820, 560, 120, 'note')
    return d.save()


DOMAINS = [  # professor id, name, port, host process key, how
    ('ms-auth', 5301, 'auth', 'propio'),
    ('ms-catalogos', 5302, 'catalog', 'compartido con Administración'),
    ('ms-sesiones-conteo', 5303, 'session', 'Operador en el portal; Supervisor en SessionService'),
    ('ms-evidencias', 5304, 'evidence', 'propio'),
    ('ms-auditoria', 5305, 'audit', 'consulta propia; escritura en cada servicio'),
    ('ms-notificaciones', 5306, 'notifications', 'propio + worker'),
    ('ms-monitoreo', 5307, 'monitoring', 'propio + collector'),
    ('ms-camaras', 5308, 'web', 'configuración/calibración en Administración; estado en Monitoring; bind en VisionWorker'),
    ('ms-video-tiempo-real', 5309, 'vision', 'compartido (latencia)'),
    ('ms-inferencia-vision', 5310, 'vision', 'compartido (latencia)'),
    ('ms-tracking-instrumental', 5311, 'vision', 'compartido (latencia); bitácora leída en SessionService'),
    ('ms-comparacion-inventario', 5312, 'session', 'compartido (consistencia transaccional)'),
    ('ms-discrepancias', 5313, 'session', 'compartido (consistencia transaccional)'),
    ('ms-validacion-humana', 5314, 'web', 'Operador en el portal; sello verificado en SessionService'),
    ('ms-supervision', 5315, 'web', 'pantallas del Supervisor → microservicios'),
    ('ms-kits-charolas', 5316, 'catalog', 'compartido con catálogos'),
    ('ms-modelos-ia', 5317, 'web', 'Administración + almacén de modelos de VisionWorker'),
    ('ms-reportes-indicadores', 5318, 'session', 'compartido (mismas tablas)'),
    ('ms-sincronizacion', 5319, 'integration', 'compartido'),
    ('ms-integracion-hospitalaria', 5320, 'integration', 'compartido + worker'),
]
HOSTS = {  # key -> (label, x, y, w, style)
    'web': ('BackendWebFlask :5000 (portal + Administración)', 20, 70, 'groupw'),
    'auth': ('AuthService :5001', 460, 70, 'group'),
    'vision': ('VisionWorker :5002', 900, 70, 'groupv'),
    'catalog': ('CatalogService :5003', 1340, 70, 'group'),
    'session': ('SessionService :5004', 20, 520, 'group'),
    'evidence': ('EvidenceService :5005', 460, 520, 'group'),
    'audit': ('AuditService :5006', 900, 520, 'group'),
    'notifications': ('NotificationsService :5007', 1340, 520, 'group'),
    'monitoring': ('MonitoringService :5008', 460, 800, 'group'),
    'integration': ('IntegrationService :5009', 900, 800, 'group'),
}


def domains() -> Path:
    d = Diagram('ms12_dominios', 1800, 1150)
    d.box('PEF — 20 dominios funcionales (puerto propuesto 53xx) dentro del proceso real que los aloja', 20, 10, 1300, 30, 'title')
    by_host: dict[str, list] = {}
    for item in DOMAINS:
        by_host.setdefault(item[2], []).append(item)
    for key, (label, x, y, style) in HOSTS.items():
        items = by_host.get(key, [])
        height = 50 + max(1, len(items)) * 62
        group = d.box(label, x, y, 420, height, style)
        for i, (name, port, _, how) in enumerate(items):
            d.box(f'{name} ({port})\n{how}', 10, 35 + i * 62, 400, 54, 'domain', parent=group)
    d.box('Lectura: cada recuadro interior es un DOMINIO; el recuadro exterior es el PROCESO (contenedor) real.\n'
          'Los dominios que comparten proceso no son contenedores propios (MS-12, Fase 2): VisionWorker agrupa video, '
          'inferencia y tracking por latencia; SessionService agrupa conteo, comparación, discrepancias y reportes por '
          'consistencia transaccional; CatalogService agrupa catálogos, kits y charolas; IntegrationService agrupa '
          'sincronización e integración hospitalaria.', 1340, 800, 420, 200, 'note')
    return d.save()


def flow() -> Path:
    d = Diagram('ms12_flujo_conteo', 1800, 900)
    d.box('PEF — Flujo de una sesión de conteo: Supervisor → Operador → VisionWorker → EvidenceService', 20, 10, 1200, 30, 'title')
    lanes = {}
    for i, (key, label, style) in enumerate((('spd', 'Supervisor (portal)', 'portal'), ('session', 'SessionService', 'process'),
                                              ('op', 'Operador (portal)', 'portal'), ('vision', 'VisionWorker', 'vision'),
                                              ('evidence', 'EvidenceService + Garage', 'process'),
                                              ('notif', 'NotificationsService', 'process'))):
        lanes[key] = d.box(label, 40 + i * 290, 70, 230, 50, style)
    steps = [
        ('spd', 'session', '1. Programa la sesión (kit → inventario esperado congelado; charola opcional)'),
        ('session', 'notif', '2. session.scheduled → aviso al Operador'),
        ('op', 'vision', '3. Inicia, elige cámara: bind firmado (modelo fijado por SHA-256, ROI, calibración)'),
        ('vision', 'op', '4. Primer fotograma → conteo inicial (referencia)'),
        ('op', 'evidence', '5. Checkpoint inicial: JPEG por el outbox (reintento por ventana de tiempo, MS-12)'),
        ('vision', 'op', '6. Video completo: tracking (eventos), último fotograma'),
        ('op', 'session', '7. Conteo final = referencia → aceptado; si no, rechazado (MS-03.1, sin cambios)'),
        ('op', 'session', '8. Bitácora de tracking persistida (tracking_event), ligada al conteo final'),
        ('op', 'session', '9. Validación humana (solo clasificación) + sello SHA-256'),
        ('session', 'notif', '10. Discrepancias → aviso al Supervisor'),
        ('spd', 'session', '11. Revisa: evidencias, bitácora, sello (intact/altered), equivalencias del kit'),
        ('spd', 'evidence', '12. Descarga de evidencias con URL firmada'),
        ('spd', 'session', '13. Resuelve y cierra (cierre seguro: SPD)'),
    ]
    for i, (a, b, text) in enumerate(steps):
        y = 150 + i * 52
        src = d.box('', 40 + list(lanes).index(a) * 290 + 110, y, 10, 10, 'rounded=0;fillColor=#000000;')
        dst = d.box('', 40 + list(lanes).index(b) * 290 + 110, y, 10, 10, 'rounded=0;fillColor=#000000;')
        d.edge(src, dst, text, 'edge_straight')
    d.box('Las líneas de vida verticales se omiten para que el diagrama siga siendo editable; cada flecha es un paso '
          'con su número. Ninguna equivalencia, bitácora o sello cambia el conteo.', 40, 840, 1200, 40, 'note')
    return d.save()


def deployment() -> Path:
    d = Diagram('ms12_despliegue_compose', 1800, 1100)
    d.box('PEF — Despliegue con Docker Compose (red pef-network)', 20, 10, 900, 30, 'title')
    net = d.box('red bridge pef-network', 20, 60, 1300, 760, 'group')
    entries = [
        ('gateway (perfil gateway)\nnginx 1.27.4\nTLS 443 / 80 → 443', 'gateway', 20, 40),
        ('web :5000', 'portal', 260, 40), ('vision :5002', 'vision', 500, 40), ('auth :5001', 'process', 740, 40),
        ('catalog :5003', 'process', 980, 40),
        ('session :5004', 'process', 20, 200), ('evidence :5005', 'process', 260, 200), ('audit :5006', 'process', 500, 200),
        ('notifications :5007', 'process', 740, 200), ('monitoring :5008', 'process', 980, 200),
        ('integration :5009', 'process', 20, 360), ('notifications-worker', 'worker', 260, 360),
        ('monitoring-collector', 'worker', 500, 360), ('integration-worker', 'worker', 740, 360),
        ('db-migrate (one-shot)\n004..019', 'worker', 980, 360),
        ('postgres 16\nhealthcheck TCP', 'store', 20, 540), ('redis 7', 'store', 260, 540), ('mongo 7', 'store', 500, 540),
        ('garage 2.4.1 (S3)', 'store', 740, 540),
    ]
    for label, style, x, y in entries:
        d.box(label, x, y, 220, 110 if style == 'store' else 90, style, parent=net)
    d.box('Volúmenes con nombre (nunca se borran: sin "down -v"):\npostgres_data, redis_data, mongo_data, garage_meta, '
          'garage_data, web_outbox, web_admin_storage, vision_data', 1340, 60, 420, 120, 'note')
    d.box('Orden de arranque: postgres (healthy por TCP) → db-migrate (completed) → servicios → gateway.\n'
          'Puertos: en producción solo el Gateway; los servicios publican 127.0.0.1:50xx para diagnóstico.\n'
          'Pila de pruebas aislada: proyecto pef_ms11 (puertos 26xxx) con HAPI FHIR de pruebas y pesos montados en solo '
          'lectura (scripts/compose_ms11.py).', 1340, 200, 420, 200, 'note')
    d.box('Pesos YOLO: best.pt provisional montado en solo lectura, nunca en el repositorio.\n'
          'Secretos: .env (no versionado); compose.env aleatorio en la pila de pruebas.', 1340, 420, 420, 120, 'note')
    return d.save()


if __name__ == '__main__':
    for build in (containers, domains, flow, deployment):
        print(build())
