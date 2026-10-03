"""Redis-backed JWT session store (PEF-DS01).

Keys:
  auth:jwt:{jti}              → JSON session metadata, TTL = JWT lifetime
  auth:login_fail:{identity}  → integer fail counter, short TTL
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import redis


def utc_now() -> datetime:
	return datetime.now(timezone.utc)


class RedisTokenStore:
	def __init__(self, redis_url: str, login_fail_ttl_seconds: int = 900, login_fail_max: int = 8):
		self.client = redis.Redis.from_url(redis_url, decode_responses=True)
		self.login_fail_ttl_seconds = max(60, login_fail_ttl_seconds)
		self.login_fail_max = max(1, login_fail_max)

	def ping(self) -> bool:
		return bool(self.client.ping())

	def _jwt_key(self, jti: str) -> str:
		return f"auth:jwt:{jti}"

	def _fail_key(self, identity: str) -> str:
		return f"auth:login_fail:{identity}"

	def store_token(
		self,
		*,
		jti: str,
		user_id: str,
		institution_id: str | None,
		token_type: str,
		expires_at: datetime,
	) -> None:
		ttl = max(1, int((expires_at - utc_now()).total_seconds()))
		payload = {
			"jti": jti,
			"user_id": user_id,
			"institution_id": institution_id,
			"token_type": token_type,
			"revoked": False,
			"exp": expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
		}
		self.client.set(self._jwt_key(jti), json.dumps(payload), ex=ttl)

	def get_token(self, jti: str) -> dict[str, Any] | None:
		raw = self.client.get(self._jwt_key(jti))
		if not raw:
			return None
		try:
			data = json.loads(raw)
		except json.JSONDecodeError:
			return None
		if not isinstance(data, dict):
			return None
		return data

	def revoke_token(self, jti: str) -> None:
		key = self._jwt_key(jti)
		raw = self.client.get(key)
		if not raw:
			return
		try:
			data = json.loads(raw)
		except json.JSONDecodeError:
			self.client.delete(key)
			return
		if not isinstance(data, dict):
			self.client.delete(key)
			return
		data["revoked"] = True
		ttl = self.client.ttl(key)
		if ttl and ttl > 0:
			self.client.set(key, json.dumps(data), ex=ttl)
		else:
			self.client.set(key, json.dumps(data), ex=60)

	def is_login_blocked(self, identity: str) -> bool:
		raw = self.client.get(self._fail_key(identity))
		if raw is None:
			return False
		try:
			return int(raw) >= self.login_fail_max
		except ValueError:
			return False

	def register_login_failure(self, identity: str) -> int:
		key = self._fail_key(identity)
		count = int(self.client.incr(key))
		if count == 1:
			self.client.expire(key, self.login_fail_ttl_seconds)
		return count

	def clear_login_failures(self, identity: str) -> None:
		self.client.delete(self._fail_key(identity))


def build_token_store() -> RedisTokenStore:
	redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
	fail_ttl = int(os.getenv("AUTH_LOGIN_FAIL_TTL_SECONDS", "900"))
	fail_max = int(os.getenv("AUTH_LOGIN_FAIL_MAX", "8"))
	return RedisTokenStore(redis_url, login_fail_ttl_seconds=fail_ttl, login_fail_max=fail_max)
