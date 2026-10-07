"""POST /preferences: partial merge into user.ui_preferences (DS01), without PostgreSQL/Redis."""

import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import jwt
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "pytest-only-secret-key-0123456789abcdef")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402

USER_ID = "22222222-2222-2222-2222-222222222222"


class FakeTokenStore:
	def __init__(self):
		self.tokens = {}

	def get_token(self, jti):
		return self.tokens.get(jti)


class FakeResult:
	def __init__(self, row):
		self._row = row

	def first(self):
		return self._row


class FakeConnection:
	def __init__(self, db):
		self.db = db

	def execute(self, statement, params=None):
		sql = str(statement)
		if "SELECT active" in sql:
			return FakeResult((True,))
		if "SELECT ui_preferences" in sql:
			assert "FOR UPDATE" in sql
			return FakeResult((dict(self.db.preferences),) if self.db.exists else None)
		if sql.startswith("UPDATE"):
			assert "updated_at = now()" in sql
			self.db.updates.append(params)
			import json
			self.db.preferences = json.loads(params["prefs"])
			return FakeResult(None)
		raise AssertionError(f"unexpected SQL: {sql}")

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


class FakeEngine:
	def __init__(self, preferences, exists=True):
		self.preferences = preferences
		self.exists = exists
		self.updates = []

	def connect(self):
		return FakeConnection(self)

	def begin(self):
		return FakeConnection(self)


@pytest.fixture
def setup(monkeypatch):
	store = FakeTokenStore()
	monkeypatch.setattr(main, "token_store", store)

	def make(preferences, exists=True):
		engine = FakeEngine(preferences, exists=exists)
		monkeypatch.setattr(main, "engine", engine)
		jti = str(uuid.uuid4())
		store.tokens[jti] = {"user_id": USER_ID, "token_type": "access", "revoked": False}
		now = main.utc_now()
		token = jwt.encode(
			{"sub": USER_ID, "jti": jti, "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
			main.app.config["JWT_SECRET_KEY"],
			algorithm=main.app.config["JWT_ALGORITHM"],
		)
		return engine, {"Authorization": f"Bearer {token}"}

	return make


def post(headers, body):
	return main.app.test_client().post("/preferences", json=body, headers=headers)


def test_locale_change_keeps_theme_and_other_keys(setup):
	engine, headers = setup({"locale": "en", "theme": "dark", "density": "compact"})
	response = post(headers, {"locale": "es-MX"})
	assert response.status_code == 200
	assert response.get_json() == {"ui_preferences": {"locale": "es-MX", "theme": "dark"}}
	assert engine.preferences == {"locale": "es-MX", "theme": "dark", "density": "compact"}
	assert len(engine.updates) == 1


def test_legacy_language_alias_is_replaced(setup):
	engine, headers = setup({"language": "en", "theme": "light"})
	assert post(headers, {"locale": "es-MX"}).status_code == 200
	assert engine.preferences == {"locale": "es-MX", "theme": "light"}


def test_theme_only_change_keeps_locale(setup):
	engine, headers = setup({"locale": "es-MX", "theme": "light"})
	response = post(headers, {"theme": "dark"})
	assert response.get_json()["ui_preferences"] == {"locale": "es-MX", "theme": "dark"}


@pytest.mark.parametrize("body", [{"locale": "fr"}, {"locale": "es"}, {"locale": None}, {"theme": "blue"}, {}])
def test_invalid_payload_is_rejected_without_writing(setup, body):
	engine, headers = setup({"locale": "en", "theme": "dark"})
	assert post(headers, body).status_code == 400
	assert engine.updates == []


def test_requires_access_token(setup):
	setup({"locale": "en"})
	assert main.app.test_client().post("/preferences", json={"locale": "es-MX"}).status_code == 401


def test_unknown_user(setup):
	engine, headers = setup({}, exists=False)
	assert post(headers, {"locale": "es-MX"}).status_code == 404
