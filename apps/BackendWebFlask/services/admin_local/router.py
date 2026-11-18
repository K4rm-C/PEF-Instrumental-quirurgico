"""Dispatch Admin RF screen operations to in-process use cases (never HTTP)."""
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException

from .errors import ApiError


class LocalAdminRouter:
    def __init__(self):
        self.entries = []
        self._map = None
        self.handlers = {}

    def _route(self, method, path):
        def decorator(handler):
            # Retain stable function name for introspection/tests.
            endpoint = handler.__name__
            if endpoint in self.handlers:
                raise RuntimeError(f'Duplicate Admin handler: {endpoint}')
            self.entries.append((method, path, endpoint))
            self.handlers[endpoint] = handler
            self._map = None
            return handler
        return decorator

    def get(self, path):
        return self._route('GET', path)

    def post(self, path):
        return self._route('POST', path)

    def put(self, path):
        return self._route('PUT', path)

    def delete(self, path):
        return self._route('DELETE', path)

    def dispatch(self, method, path):
        if self._map is None:
            self._map = Map([Rule(path, endpoint=name, methods=[verb])
                             for verb, path, name in self.entries])
        try:
            endpoint, values = self._map.bind('localhost').match(path, method=method)
        except HTTPException as exc:
            raise ApiError(exc.code or 404, 'not_found', 'Admin operation not found.') from exc
        return self.handlers[endpoint](**values)
