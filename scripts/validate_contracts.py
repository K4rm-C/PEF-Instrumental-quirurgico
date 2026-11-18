"""MS-04: contract validation of the microservices against their OWN published documents.

For every GET operation of each service's openapi.yaml (documentation, Swagger and health routes apart) the
validator calls the running service — through the API Gateway for the published services, directly for the
unpublished ones — once asking JSON and once asking XML, and checks:

- the HTTP status is documented for that operation;
- JSON: the body validates against the documented `application/json` schema (OpenAPI 3.0 → JSON Schema,
  `nullable` converted, `$ref` resolved inside the same document);
- XML: `application/xml` is documented, the body is well formed and validates against the service's XSD;
- error responses (anonymous → 401, unknown id → 404) follow the documented error schema too;
- the openapi.yaml / XSD the service serves are byte-identical to the files in the repository.

Used by scripts/e2e_gateway.py (real services and tokens). Requires jsonschema and lxml.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from jsonschema import Draft7Validator
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIXES = ('/docs', '/openapi.yaml', '.xsd')


@dataclass
class Service:
    name: str
    folder: str
    base: str                      # base URL used for the API (gateway or direct)
    token: str                     # Bearer of a role allowed to read
    xsd: str | None                # main response XSD (file name in schemas/)
    ids: dict[str, str] = field(default_factory=dict)   # path template → value for its {id}/{name}
    docs_base: str | None = None   # where openapi.yaml / XSD are served (defaults to base)
    include_health: bool = False
    health_base: str | None = None
    # Paths deliberately NOT published by the gateway (its 404 is intended): path → why / where it is tested.
    unpublished: dict[str, str] = field(default_factory=dict)


@dataclass
class Result:
    service: str
    check: str
    ok: bool
    detail: str = ''
    kind: str = 'ok'      # ok | violation (contradicts the contract) | gap (not covered by the documents)


GAP_MARKERS = ('declares no root', 'no XSD published for this service')


def _kind(check: str, problem: str | None) -> str:
    if problem is None:
        return 'ok'
    if any(marker in problem for marker in GAP_MARKERS):
        return 'gap'
    if 'GET /health' in check and 'not documented for this status' in problem:
        return 'gap'
    return 'violation'


def _openapi_to_jsonschema(node):
    """OpenAPI 3.0 schema objects → JSON Schema (draft 7): `nullable: true` adds "null" to the type."""
    if isinstance(node, list):
        return [_openapi_to_jsonschema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _openapi_to_jsonschema(value) for key, value in node.items()
           if key not in ('example', 'examples', 'xml', 'discriminator', 'externalDocs', 'deprecated')}
    if out.pop('nullable', False):
        if 'enum' in out and None not in out['enum']:
            out['enum'] = [*out['enum'], None]      # OAS 3.0: nullable + enum also admits null
        if 'type' in out:
            out['type'] = [out['type'], 'null'] if isinstance(out['type'], str) else [*out['type'], 'null']
        elif '$ref' in out or 'allOf' in out or 'oneOf' in out or 'anyOf' in out:
            out = {'anyOf': [out, {'type': 'null'}]}
    return out


def _resolve(spec, node):
    while isinstance(node, dict) and '$ref' in node and len(node) == 1:
        parts = node['$ref'].lstrip('#/').split('/')
        node = spec
        for part in parts:
            node = node[part]
    return node


class Contract:
    def __init__(self, service: Service):
        self.service = service
        folder = ROOT / 'apps' / service.folder
        self.spec_text = (folder / 'openapi.yaml').read_bytes()
        self.spec = yaml.safe_load(self.spec_text)
        self.components = _openapi_to_jsonschema(self.spec.get('components', {}))
        self.xsd_path = folder / 'schemas' / service.xsd if service.xsd else None
        self.schema = etree.XMLSchema(etree.parse(str(self.xsd_path))) if self.xsd_path else None

    def response(self, path: str, method: str, status: int):
        operation = self.spec['paths'][path][method]
        responses = operation.get('responses', {})
        for key in (str(status), f'{str(status)[0]}XX', 'default'):
            if key in responses:
                return _resolve(self.spec, responses[key])
        return None

    def validate_json(self, response_doc, body):
        content = (response_doc or {}).get('content', {})
        media = content.get('application/json') or content.get('application/problem+json')
        if not media or 'schema' not in media:
            return 'application/json not documented for this status'
        schema = dict(_openapi_to_jsonschema(media['schema']))
        schema['components'] = self.components
        errors = sorted(Draft7Validator(schema).iter_errors(body), key=lambda e: list(e.absolute_path))
        if errors:
            distinct = []
            for error in errors:
                described = f'/{"/".join(map(str, error.absolute_path))}: {error.message[:120]}'
                if described not in distinct:
                    distinct.append(described)
            return f'{len(errors)} error(s): ' + ' | '.join(distinct[:3])
        return None

    def validate_xml(self, response_doc, raw: bytes):
        content = (response_doc or {}).get('content', {})
        if 'application/xml' not in content:
            return 'application/xml not documented for this status'
        try:
            document = etree.fromstring(raw)
        except etree.XMLSyntaxError as exc:
            return f'not well formed: {exc}'
        if self.schema is None:
            return 'no XSD published for this service'
        if not self.schema.validate(document):
            return f'XSD: {self.schema.error_log.last_error}'
        return None


def _call(base, path, token, accept):
    headers = {'Accept': accept}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return requests.get(base + path, headers=headers, timeout=30)


def validate_service(service: Service) -> list[Result]:
    contract = Contract(service)
    results: list[Result] = []

    def add(check, problem):
        if problem and 'No matching global declaration' in problem:
            root = problem.split("Element '")[1].split("'")[0] if "Element '" in problem else '?'
            problem = f'XSD {service.xsd} declares no root <{root}>'
        results.append(Result(service.name, check, problem is None, problem or '', _kind(check, problem)))

    # Served documents = repository documents.
    docs_base = service.docs_base or service.base
    for path in contract.spec['paths']:
        if path.endswith('/openapi.yaml') or path.endswith('.xsd'):
            served = requests.get(docs_base + path, timeout=30)
            local = (contract.spec_text if path.endswith('/openapi.yaml')
                     else (ROOT / 'apps' / service.folder / 'schemas' / path.rsplit('/', 1)[1]).read_bytes())
            same = served.status_code == 200 and served.content.replace(b'\r\n', b'\n') == local.replace(b'\r\n', b'\n')
            add(f'GET {path} served = repository file', None if same else f'HTTP {served.status_code}, differs')

    first_list = None
    for path, item in contract.spec['paths'].items():
        if 'get' not in item or path.endswith(SKIP_SUFFIXES):
            continue
        if path in service.unpublished:
            results.append(Result(service.name, f'GET {path}', True,
                                  f'not exercised (not published by the gateway: {service.unpublished[path]})'))
            continue
        is_health = path.startswith('/health')
        if is_health and not service.include_health:
            continue
        base = (service.health_base or service.base) if is_health else service.base
        if '{' in path:
            value = service.ids.get(path)
            if value is None:
                results.append(Result(service.name, f'GET {path}', True, 'not exercised (no sample id)'))
                continue
            concrete = path[:path.index('{')] + value + path[path.index('}') + 1:]
        else:
            concrete = path
            first_list = first_list or (path if not is_health else None)
        for accept in ('application/json', 'application/xml'):
            response = _call(base, concrete, None if is_health else service.token, accept)
            documented = contract.response(path, 'get', response.status_code)
            label = f'GET {path} [{accept.split("/")[1].upper()}] {response.status_code}'
            if documented is None:
                add(label, f'status {response.status_code} not documented')
                continue
            if accept.endswith('json'):
                try:
                    body = response.json()
                except ValueError:
                    add(label, f'not JSON (Content-Type {response.headers.get("Content-Type")})')
                    continue
                add(label, contract.validate_json(documented, body))
            else:
                if 'xml' not in response.headers.get('Content-Type', ''):
                    if 'application/xml' in (documented.get('content') or {}):
                        add(label, f'XML documented but served {response.headers.get("Content-Type")}')
                    else:
                        add(label + ' (JSON only, as documented)', None)
                    continue
                add(label, contract.validate_xml(documented, response.content))
        if '{' in path:
            missing = path[:path.index('{')] + str(uuid.uuid4()) + path[path.index('}') + 1:]
            for accept in ('application/json', 'application/xml'):
                response = _call(base, missing, service.token, accept)
                documented = contract.response(path, 'get', response.status_code)
                label = f'GET {path} unknown id [{accept.split("/")[1].upper()}] {response.status_code}'
                if response.status_code != 404 or documented is None:
                    add(label, f'expected a documented 404, got {response.status_code}')
                elif accept.endswith('json'):
                    add(label, contract.validate_json(documented, response.json()))
                elif 'xml' in response.headers.get('Content-Type', ''):
                    add(label, contract.validate_xml(documented, response.content))
                else:
                    add(label + ' (JSON error body)', None if 'application/xml' not in
                        (documented.get('content') or {}) else 'XML error documented but not served')
    if first_list:
        for accept in ('application/json', 'application/xml'):
            response = _call(service.base, first_list, None, accept)
            documented = contract.response(first_list, 'get', response.status_code)
            label = f'GET {first_list} anonymous [{accept.split("/")[1].upper()}] {response.status_code}'
            if response.status_code != 401 or documented is None:
                add(label, f'expected a documented 401, got {response.status_code}')
            elif accept.endswith('json'):
                add(label, contract.validate_json(documented, response.json()))
            elif 'xml' in response.headers.get('Content-Type', ''):
                add(label, contract.validate_xml(documented, response.content))
            else:
                add(label + ' (JSON error body)', None if 'application/xml' not in
                    (documented.get('content') or {}) else 'XML error documented but not served')
    return results


def validate(services: list[Service]) -> list[Result]:
    results: list[Result] = []
    for service in services:
        try:
            results.extend(validate_service(service))
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            results.append(Result(service.name, 'validator', False, f'{type(exc).__name__}: {exc}', 'violation'))
    return results


def summary(results: list[Result]) -> str:
    lines = []
    for service in dict.fromkeys(r.service for r in results):
        own = [r for r in results if r.service == service]
        violations = [r for r in own if r.kind == 'violation']
        gaps = [r for r in own if r.kind == 'gap']
        lines.append(f'{service}: {sum(r.ok for r in own)}/{len(own)} OK, {len(violations)} violation(s), '
                     f'{len(gaps)} gap(s)')
        lines.extend(f'    VIOLATION {r.check}: {r.detail}' for r in violations)
        lines.extend(f'    GAP {r.check}: {r.detail}' for r in gaps)
    return '\n'.join(lines)


if __name__ == '__main__':  # pragma: no cover - documentation of the entry point
    raise SystemExit('Run through scripts/e2e_gateway.py (needs running services, tokens and sample ids).')
