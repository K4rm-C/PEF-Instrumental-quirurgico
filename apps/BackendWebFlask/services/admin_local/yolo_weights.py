"""Read class names from an uploaded YOLO `.pt` WITHOUT executing it (RF-AD-08).

A PyTorch checkpoint is a zip whose `*/data.pkl` is a pickle; loading it with torch/pickle would
import and run arbitrary code chosen by whoever produced the file. Here the pickle is walked with an
Unpickler whose `find_class` never imports anything: every global becomes an inert `_Stub` that only
records its constructor arguments and state, and tensor storages (`persistent_load`) become None.
No module of the file is imported, no reducer of the file is called, nothing is written to disk.
From the resulting inert graph we look for `names` ({id: name} or [name, ...]), `nc`, and the
architecture / scale of Ultralytics checkpoints (`yaml`: `yaml_file`, `scale`).

Fallback (RF-AD-08 "si el parse falla, pide names.yaml o JSON"): parse_names_file() reads
`{"0": "name"}` / `["name", ...]` JSON or the `names:` mapping/list of a data YAML (simple subset,
no YAML library and no YAML tags executed).
"""
import io
import json
import pickle
import re
import zipfile

MAX_PICKLE_BYTES = 64 * 1024 * 1024
MAX_CLASSES = 1000
MAX_NODES = 200000


class WeightsParseError(Exception):
    pass


class _Stub(dict):
    """Inert stand-in for any class/function referenced by the pickle (dict so SETITEMS works)."""

    def __new__(cls, *args, **kwargs):  # NEWOBJ calls __new__ only; REDUCE calls the class
        obj = super().__new__(cls)
        obj._args, obj._items, obj._state = args, [], None
        return obj

    def __init__(self, *args, **kwargs):
        super().__init__()

    def __setstate__(self, state):
        self._state = state

    def append(self, value):
        self._items.append(value)

    def extend(self, values):
        self._items.extend(values)


def _stub_class(module, name):
    return type(f'Stub_{name}', (_Stub,), {'__module__': 'pef.inert', '_origin': f'{module}.{name}'})


class _InertUnpickler(pickle.Unpickler):
    def find_class(self, module, name):  # never imports
        return _stub_class(module, name)

    def persistent_load(self, pid):  # tensor storages: not loaded
        return None


def _children(node):
    if isinstance(node, dict):
        yield from node.items()
        if isinstance(node, _Stub):
            yield '_args', node._args
            yield '_items', node._items
            yield '_state', node._state
            yield '__dict__', vars(node)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for item in node:
            yield None, item


def _as_names(value):
    """{0: 'a', 1: 'b'} / ['a', 'b'] -> {0: 'a', 1: 'b'}; None if it is not a names table."""
    if isinstance(value, _Stub) and value._items and not dict.__len__(value):
        value = value._items
    if isinstance(value, dict) and not isinstance(value, _Stub) and value:
        try:
            names = {int(key): str(name) for key, name in value.items() if isinstance(name, str)}
        except (TypeError, ValueError):
            return None
        return names if len(names) == len(value) else None
    if isinstance(value, (list, tuple)) and value and all(isinstance(name, str) for name in value):
        return dict(enumerate(value))
    return None


def _walk(root):
    found = {'names': None, 'nc': None, 'yaml': None}
    stack, seen, visited = [root], set(), 0
    while stack and visited < MAX_NODES:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        visited += 1
        for key, child in _children(node):
            if key == 'names' and found['names'] is None:
                found['names'] = _as_names(child)
            elif key == 'nc' and found['nc'] is None and isinstance(child, int):
                found['nc'] = child
            elif key == 'yaml' and found['yaml'] is None and isinstance(child, dict) and not isinstance(child, _Stub):
                found['yaml'] = child
            if isinstance(child, (dict, list, tuple, set, frozenset)):
                stack.append(child)
    return found


def _validate(names):
    if not names or len(names) > MAX_CLASSES:
        raise WeightsParseError('no usable class names')
    if sorted(names) != list(range(len(names))):
        raise WeightsParseError('class ids must be 0..n-1')
    cleaned = {class_id: name.strip()[:160] for class_id, name in names.items()}
    if any(not name for name in cleaned.values()):
        raise WeightsParseError('empty class name')
    return cleaned


def read_checkpoint(path):
    """{'names': {id: name}, 'nc', 'architecture', 'model_size'} from a .pt zip; WeightsParseError if not possible."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if info.filename.endswith('data.pkl')]
            if not members:
                raise WeightsParseError('no data.pkl in the checkpoint')
            member = min(members, key=lambda info: info.filename.count('/'))
            if member.file_size > MAX_PICKLE_BYTES:
                raise WeightsParseError('checkpoint metadata too large')
            payload = archive.read(member)
    except (zipfile.BadZipFile, OSError) as exc:
        raise WeightsParseError('not a PyTorch zip checkpoint') from exc
    try:
        root = _InertUnpickler(io.BytesIO(payload)).load()
    except Exception as exc:  # noqa: BLE001 - any malformed pickle is just "cannot parse"
        raise WeightsParseError(f'unreadable checkpoint ({type(exc).__name__})') from exc
    found = _walk(root)
    names = _validate(found['names'] or {})
    if found['nc'] is not None and found['nc'] != len(names):
        raise WeightsParseError('nc does not match the class names')
    yaml = found['yaml'] or {}
    yaml_file = str(yaml.get('yaml_file') or '')
    architecture = re.sub(r'\.ya?ml$', '', yaml_file.rsplit('/', 1)[-1]) or None
    scale = yaml.get('scale')
    if not scale and architecture:
        match = re.match(r'^yolo(?:v?\d+)([nsmlx])\b', architecture)
        scale = match.group(1) if match else None
    return {'names': names, 'nc': len(names), 'architecture': (architecture or None) and architecture[:64],
            'model_size': str(scale)[:8] if scale else None}


def parse_names_file(content):
    """Class names from JSON ({"0": "a"} or ["a"]) or a data YAML `names:` block; WeightsParseError if not."""
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise WeightsParseError('names file must be UTF-8 text') from exc
    stripped = text.strip()
    if stripped.startswith(('{', '[')):
        try:
            data = json.loads(stripped)
        except ValueError as exc:
            raise WeightsParseError('invalid JSON') from exc
        if isinstance(data, dict) and 'names' in data:
            data = data['names']
        return _validate(_as_names(data) or {})
    return _validate(_yaml_names(text))


def _yaml_scalar(value):
    value = value.split(' #', 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
        value = value[1:-1]
    return value


def _yaml_names(text):
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    names, in_block, block_indent = {}, False, None
    for line in lines:
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if not in_block:
            match = re.match(r'^names\s*:\s*(.*)$', body)
            if match and indent == 0:
                rest = match.group(1).strip()
                if rest.startswith('['):
                    items = [_yaml_scalar(item) for item in rest.strip('[]').split(',') if item.strip()]
                    return dict(enumerate(items))
                in_block = True
            continue
        if indent == 0:
            break
        block_indent = block_indent if block_indent is not None else indent
        mapping = re.match(r'^(\d+)\s*:\s*(.+)$', body)
        if mapping:
            names[int(mapping.group(1))] = _yaml_scalar(mapping.group(2))
        elif body.startswith('- '):
            names[len(names)] = _yaml_scalar(body[2:])
    return names
