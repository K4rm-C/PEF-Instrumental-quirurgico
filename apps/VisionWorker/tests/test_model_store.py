"""MS-02 / P2: the worker infers with the verified weights of the model pinned to each session.

Two layers:
* logic (always): reference validation, SHA-256 verification, storage sources, download errors, LRU cache,
  class compatibility, the signed bind — with a fake loader (no torch work);
* REAL inference (needs PEF_REAL_WEIGHTS = path of the provisional best.pt, mounted read-only by
  scripts/run_ms_tests.py --weights): the real YOLO is loaded through the same path, its class names are the
  17 registered ones, a frame is inferred with it, and a second, genuinely different YOLO model (built from the
  ultralytics yolov8n.yaml architecture, untrained, NOT derived from best.pt) is loaded for another session.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import requests

import bind_auth
import inference
import main
import model_store
from model_store import ModelCache, ModelSpec, ModelUnavailable, parse_spec

REAL = os.environ.get("PEF_REAL_WEIGHTS")
SHA_A, SHA_B = "a" * 64, "b" * 64
KEY = "models/" + "0" * 32 + ".pt"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeModel:
    def __init__(self, path, names=None):
        self.path = Path(path)
        self.names = names or {0: "Kelly", 1: "Mayo"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated cache dir, storage dir and no bundled file."""
    cache, storage = tmp_path / "cache", tmp_path / "storage"
    (storage / "models").mkdir(parents=True)
    monkeypatch.setattr(model_store, "CACHE_DIR", cache)
    monkeypatch.setattr(model_store, "STORAGE_DIR", str(storage))
    monkeypatch.setattr(model_store, "WEIGHTS_PATH", tmp_path / "absent.pt")
    monkeypatch.setattr(model_store, "ALLOWED_FETCH_HOSTS", {"garage:3900"})
    return storage


def _stored(storage, data: bytes) -> tuple[str, str]:
    key = "models/" + _sha(data)[:32] + ".pt"
    (storage / key).write_bytes(data)
    return key, _sha(data)


# --- reference validation ------------------------------------------------------------------------

@pytest.mark.parametrize("raw, code", [
    (None, "model_required"),
    ({"sha256": "nope"}, "model_reference_invalid"),
    ({"sha256": SHA_A, "storage": {"key": "../../etc/passwd"}}, "model_reference_invalid"),
    ({"sha256": SHA_A, "storage": {"key": "/models/best.pt"}}, "model_reference_invalid"),
    ({"sha256": SHA_A, "storage": {"key": "weights/best.pt"}}, "model_reference_invalid"),
])
def test_only_server_references_are_accepted(raw, code):
    with pytest.raises(ModelUnavailable) as exc:
        parse_spec(raw)
    assert exc.value.code == code


# --- sources and integrity -----------------------------------------------------------------------

def test_local_storage_weights_are_verified(store):
    key, sha = _stored(store, b"weights-one")
    path, source = model_store.resolve_weights(ModelSpec("m1", "v1", sha, bucket="local", key=key))
    assert source == "local_storage" and path.read_bytes() == b"weights-one"


def test_missing_weights_file_is_a_clear_error(store):
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(ModelSpec("m1", "v1", SHA_A, bucket="local", key=KEY))
    assert (exc.value.code, exc.value.status) == ("model_weights_missing", 404)


def test_wrong_sha256_is_refused_and_never_replaced(store):
    key, _ = _stored(store, b"weights-one")
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(ModelSpec("m1", "v1", SHA_B, bucket="local", key=key))
    assert exc.value.code == "model_checksum_mismatch"


def test_model_without_weights_is_not_served(store):
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(ModelSpec("seed", "yolo26l-demo", SHA_A))
    assert exc.value.code == "model_weights_missing"


def test_bundled_file_is_used_only_when_its_sha256_matches(store, tmp_path, monkeypatch):
    bundled = tmp_path / "best.pt"
    bundled.write_bytes(b"bundled-weights")
    monkeypatch.setattr(model_store, "WEIGHTS_PATH", bundled)
    path, source = model_store.resolve_weights(ModelSpec("m", "v", _sha(b"bundled-weights")))
    assert (path, source) == (bundled, "bundled")
    with pytest.raises(ModelUnavailable):            # another model: the bundled file is NOT a fallback
        model_store.resolve_weights(ModelSpec("m2", "v2", SHA_B, bucket="local", key=KEY))


class FakeResponse:
    def __init__(self, status=200, chunks=(b"",)):
        self.status_code, self._chunks = status, chunks

    def iter_content(self, _size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_s3_download_is_verified_then_cached(store, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or FakeResponse(200, (b"s3-", b"weights")))
    spec = ModelSpec("m", "v", _sha(b"s3-weights"), bucket="pef-admin", key=KEY, url="http://garage:3900/pef-admin/x?sig")
    path, source = model_store.resolve_weights(spec)
    assert source == "s3" and path.read_bytes() == b"s3-weights"
    path2, source2 = model_store.resolve_weights(spec)
    assert source2 == "cache" and len(calls) == 1      # no second download


def test_s3_download_with_wrong_checksum_is_discarded(store, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResponse(200, (b"tampered",)))
    spec = ModelSpec("m", "v", SHA_A, bucket="pef-admin", key=KEY, url="http://garage:3900/x")
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(spec)
    assert exc.value.code == "model_checksum_mismatch"
    assert not list((model_store.CACHE_DIR).glob("*.pt"))


@pytest.mark.parametrize("behaviour", ["http_error", "connection_error"])
def test_garage_failures_are_reported(store, monkeypatch, behaviour):
    def fake_get(url, **kw):
        if behaviour == "connection_error":
            raise requests.ConnectionError("down")
        return FakeResponse(500)
    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(ModelSpec("m", "v", SHA_A, bucket="pef-admin", key=KEY, url="http://garage:3900/x"))
    assert (exc.value.code, exc.value.status) == ("model_storage_unavailable", 503)


def test_download_host_must_be_allowed(store):
    spec = ModelSpec("m", "v", SHA_A, bucket="pef-admin", key=KEY, url="http://attacker.example/x")
    with pytest.raises(ModelUnavailable) as exc:
        model_store.resolve_weights(spec)
    assert exc.value.code == "model_reference_invalid"


def test_corrupted_cache_entry_is_not_used(store):
    model_store.CACHE_DIR.mkdir(parents=True)
    (model_store.CACHE_DIR / f"{SHA_A}.pt").write_bytes(b"corrupted")
    with pytest.raises(ModelUnavailable):
        model_store.resolve_weights(ModelSpec("m", "v", SHA_A, bucket="local", key=KEY))
    assert not (model_store.CACHE_DIR / f"{SHA_A}.pt").exists()


# --- memory cache and class compatibility --------------------------------------------------------

def test_loaded_model_is_reused_and_a_different_one_is_loaded(store):
    key1, sha1 = _stored(store, b"model-one")
    key2, sha2 = _stored(store, b"model-two")
    cache = ModelCache(loader=FakeModel, size=2)
    s1, s2 = ModelSpec("m1", "v1", sha1, "local", key1), ModelSpec("m2", "v2", sha2, "local", key2)
    cache.prepare(s1, [0])
    cache.prepare(s1, [0])
    assert cache.loads == 1 and cache.get(sha1) is cache.get(sha1)          # reused, no reload per frame
    cache.prepare(s2, [1])
    assert cache.loads == 2 and cache.get(sha1).path.read_bytes() == b"model-one"
    assert cache.get(sha2).path.read_bytes() == b"model-two"


def test_evicted_model_is_reloaded_and_verified_again(store):
    key1, sha1 = _stored(store, b"model-one")
    key2, sha2 = _stored(store, b"model-two")
    cache = ModelCache(loader=FakeModel, size=1)
    cache.prepare(ModelSpec("m1", "v1", sha1, "local", key1))
    cache.prepare(ModelSpec("m2", "v2", sha2, "local", key2))
    assert cache.get(sha1).path.read_bytes() == b"model-one" and cache.loads == 3


def test_unknown_sha256_is_never_guessed():
    with pytest.raises(ModelUnavailable) as exc:
        ModelCache(loader=FakeModel).get(SHA_A)
    assert exc.value.code == "model_not_bound"


@pytest.mark.parametrize("names, class_ids", [
    ({0: "Kelly", 1: "Other"}, [0]),        # names differ from the registered ones
    (None, [0, 7]),                          # class map uses a class the model does not have
])
def test_incompatible_classes_are_refused(store, names, class_ids):
    key, sha = _stored(store, b"model-one")
    spec = ModelSpec("m", "v", sha, "local", key, class_names=names)
    with pytest.raises(ModelUnavailable) as exc:
        ModelCache(loader=FakeModel).prepare(spec, class_ids)
    assert exc.value.code == "model_classes_incompatible"


# --- signed bind ---------------------------------------------------------------------------------

@pytest.fixture
def worker(monkeypatch, store):
    monkeypatch.setattr(main, "_sessions", {})
    cache = ModelCache(loader=FakeModel)
    monkeypatch.setattr(main, "MODEL_CACHE", cache)
    monkeypatch.setattr(main, "ALLOW_UNPINNED_BIND", False)
    return main.app.test_client(), cache


def _bind(client, session_id, body, signed=True):
    raw = json.dumps(body).encode()
    headers = {"X-PEF-Signature": bind_auth.sign(raw)} if signed else {}
    return client.post(f"/sessions/{session_id}/bind", data=raw, content_type="application/json", headers=headers)


def _body(key, sha, version="v1"):
    return {"expected": [], "class_map": [{"yolo_class_id": 0, "family_code": "A"}],
            "model": {"model_id": "m-" + sha[:4], "model_version": version, "sha256": sha,
                      "storage": {"bucket": "local", "key": key}}}


def test_bind_must_be_signed_and_pinned(worker, store):
    client, _ = worker
    key, sha = _stored(store, b"model-one")
    assert _bind(client, "s1", _body(key, sha), signed=False).status_code == 401
    raw = json.dumps(_body(key, sha)).encode()
    forged = client.post("/sessions/s1/bind", data=raw, content_type="application/json",
                         headers={"X-PEF-Signature": "sha256=" + "0" * 64})
    assert forged.status_code == 401
    unpinned = _bind(client, "s1", {"expected": [], "class_map": []})
    assert unpinned.status_code == 422 and unpinned.get_json()["code"] == "model_required"


def test_bind_loads_the_pinned_weights_and_never_swaps_them(worker, store):
    client, cache = worker
    key1, sha1 = _stored(store, b"model-one")
    key2, sha2 = _stored(store, b"model-two")
    first = _bind(client, "s1", _body(key1, sha1))
    assert first.status_code == 200 and first.get_json()["model"]["sha256"] == sha1
    assert first.get_json()["session"]["model_sha256"] == sha1
    assert _bind(client, "s1", _body(key1, sha1)).status_code == 200         # reconnect / re-bind: same weights
    swap = _bind(client, "s1", _body(key2, sha2, "v2"))
    assert swap.status_code == 409 and swap.get_json()["code"] == "model_conflict"
    other = _bind(client, "s2", _body(key2, sha2, "v2"))                       # another session, another model
    assert other.status_code == 200 and cache.loads == 2
    assert main._sessions["s1"].model_sha256 == sha1 and main._sessions["s2"].model_sha256 == sha2


def test_bind_reports_model_errors_without_binding(worker, store):
    client, _ = worker
    response = _bind(client, "s1", _body(KEY, SHA_A))
    assert response.status_code == 404 and response.get_json()["code"] == "model_weights_missing"
    assert "s1" not in main._sessions


# --- REAL weights (provisional best.pt) ----------------------------------------------------------

real = pytest.mark.skipif(not REAL, reason="real weights not provided (run_ms_tests.py --weights)")


@pytest.fixture
def real_store(store):
    data = Path(REAL).read_bytes()
    key = "models/" + _sha(data)[:32] + ".pt"
    shutil.copyfile(REAL, store / key)
    return key, _sha(data)


@real
def test_real_best_pt_is_loaded_verified_and_infers(real_store):
    key, sha = real_store
    from ultralytics import YOLO
    names = dict(YOLO(REAL).names)
    cache = ModelCache(size=2)           # real ultralytics loader
    info = cache.prepare(ModelSpec("best", "best-provisional", sha, "local", key, class_names=names), [0, 16])
    model = cache.get(sha)
    assert info["sha256"] == sha and len(model.names) == 17 and model.names[16] == "Surgical probe"
    assert hashlib.sha256(Path(model.ckpt_path).read_bytes()).hexdigest() == sha       # the very file verified
    state = inference.SessionState(session_id="real-1", model_sha256=sha, model_version="best-provisional")
    monkey_cache = model_store.MODEL_CACHE
    try:
        model_store.MODEL_CACHE = cache
        assert inference.get_model(sha) is model
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        inference.run_frame(frame, state)
    finally:
        model_store.MODEL_CACHE = monkey_cache
    assert state.frames_processed >= 0 and state.to_public()["model_sha256"] == sha


@real
def test_two_sessions_use_two_real_different_models(real_store, store, tmp_path):
    key, sha = real_store
    from ultralytics import YOLO
    other = YOLO("yolov8n.yaml")          # architecture shipped with ultralytics; untrained, 80 classes
    other_path = tmp_path / "other.pt"
    other.save(str(other_path))
    data = other_path.read_bytes()
    other_key = "models/" + _sha(data)[:32] + ".pt"
    (store / other_key).write_bytes(data)
    cache = ModelCache(size=2)
    cache.prepare(ModelSpec("best", "best-provisional", sha, "local", key), [0])
    cache.prepare(ModelSpec("yolov8n", "untrained-n", _sha(data), "local", other_key), [0])
    assert cache.loads == 2
    assert len(cache.get(sha).names) == 17 and len(cache.get(_sha(data)).names) == 80
    assert cache.get(sha) is not cache.get(_sha(data))
