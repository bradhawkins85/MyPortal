"""Tray devices prove possession of their own key when the WebSocket connects."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException

from app.api.routes import tray as routes
from app.core.database import Database
from app.repositories import tray as tray_repo
from app.schemas.tray import TrayDeviceKeyRequest, TrayEnrolRequest
from app.services import audit as audit_service
from app.services import tray_device_key


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(public).decode()


def _sign(private: Ed25519PrivateKey, uid: str, nonce: str) -> str:
    return base64.b64encode(private.sign(tray_device_key.challenge_message(uid, nonce))).decode()


class FakeSocket:
    def __init__(self, reply=None, delay: float = 0.0):
        self.sent: list[dict] = []
        self.reply = reply
        self.delay = delay

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_json(self):
        await asyncio.sleep(self.delay)
        return self.reply(self.sent[-1]) if callable(self.reply) else self.reply


def test_migration_adds_the_key_columns():
    sql = Path("migrations/550_tray_device_key.sql").read_text()
    assert "-- phase: expand" in sql
    assert "ADD COLUMN IF NOT EXISTS public_key " in sql
    assert "ADD COLUMN IF NOT EXISTS public_key_registered_at" in sql


def test_public_keys_are_validated_and_canonical():
    _, public = _key()
    assert tray_device_key.normalise_public_key(" " + public + " ") == public
    for bad in ("", "not base64!", base64.b64encode(b"x" * 31).decode()):
        with pytest.raises(ValueError):
            tray_device_key.normalise_public_key(bad)


def test_challenge_message_is_fixed():
    assert tray_device_key.challenge_message("abc", "n1") == b"myportal-tray-ws-v1\ndevice_uid=abc\nnonce=n1\n"


@pytest.mark.anyio
async def test_device_with_a_key_must_sign_the_challenge():
    private, public = _key()
    device = {"device_uid": "uid-1", "public_key": public}

    good = FakeSocket(lambda challenge: {"type": "auth", "signature": _sign(private, "uid-1", challenge["nonce"])})
    assert await tray_device_key.prove_possession(good, device)
    assert good.sent[0]["type"] == "challenge" and len(good.sent[0]["nonce"]) >= 40

    other, _ = _key()
    wrong_key = FakeSocket(lambda c: {"type": "auth", "signature": _sign(other, "uid-1", c["nonce"])})
    assert not await tray_device_key.prove_possession(wrong_key, device)

    wrong_device = FakeSocket(lambda c: {"type": "auth", "signature": _sign(private, "uid-2", c["nonce"])})
    assert not await tray_device_key.prove_possession(wrong_device, device)

    replay = FakeSocket({"type": "auth", "signature": _sign(private, "uid-1", "old-nonce")})
    assert not await tray_device_key.prove_possession(replay, device)

    only_pongs = FakeSocket({"type": "pong"})
    assert not await tray_device_key.prove_possession(only_pongs, device)

    garbage = FakeSocket({"type": "auth", "signature": "!!"})
    assert not await tray_device_key.prove_possession(garbage, device)


@pytest.mark.anyio
async def test_challenge_times_out(monkeypatch):
    _, public = _key()
    monkeypatch.setattr(tray_device_key, "CHALLENGE_TIMEOUT_SECONDS", 0.01)
    slow = FakeSocket({"type": "pong"}, delay=1)
    assert not await tray_device_key.prove_possession(slow, {"device_uid": "u", "public_key": public})


@pytest.mark.anyio
async def test_device_without_a_key_is_not_challenged():
    socket = FakeSocket()
    assert await tray_device_key.prove_possession(socket, {"device_uid": "u", "public_key": None})
    assert socket.sent == []


@pytest.fixture
async def key_db(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    adapter = Database()
    adapter._use_sqlite = True
    adapter._sqlite_conn = conn
    monkeypatch.setattr(tray_repo, "db", adapter)
    await conn.executescript(
        "CREATE TABLE tray_devices (id INTEGER PRIMARY KEY, device_uid TEXT, status TEXT, "
        "public_key TEXT, public_key_registered_at TEXT, updated_at TEXT);"
        "INSERT INTO tray_devices (id, device_uid, status) VALUES (1, 'uid-1', 'active');"
    )
    audits: list[str] = []

    async def log_action(**kwargs):
        audits.append(kwargs["action"])

    monkeypatch.setattr(audit_service, "log_action", log_action)
    yield audits
    await conn.close()


@pytest.mark.anyio
async def test_a_device_registers_its_key_once(key_db):
    _, public = _key()
    _, other = _key()
    request = SimpleNamespace()

    device = await tray_repo.get_device_by_id(1)
    assert (await routes.register_device_key(TrayDeviceKeyRequest(public_key=public), request, device)).status == "registered"
    stored = await tray_repo.get_device_by_id(1)
    assert stored["public_key"] == public and stored["public_key_registered_at"]

    assert (await routes.register_device_key(TrayDeviceKeyRequest(public_key=public), request, stored)).status == "unchanged"
    # A stale device row (another request registered first) is re-read.
    assert (await routes.register_device_key(TrayDeviceKeyRequest(public_key=public), request, device)).status == "unchanged"

    with pytest.raises(HTTPException) as refused:
        await routes.register_device_key(TrayDeviceKeyRequest(public_key=other), request, stored)
    assert refused.value.status_code == 409
    assert (await tray_repo.get_device_by_id(1))["public_key"] == public
    assert key_db == ["tray_device_key_registered", "tray_device_key_conflict"]

    await tray_repo.set_device_public_key(1, None)
    cleared = await tray_repo.get_device_by_id(1)
    assert cleared["public_key"] is None and cleared["public_key_registered_at"] is None


@pytest.mark.anyio
async def test_register_rejects_a_malformed_key(key_db):
    with pytest.raises(HTTPException) as refused:
        await routes.register_device_key(
            TrayDeviceKeyRequest(public_key="A" * 44), SimpleNamespace(), {"id": 1, "public_key": None}
        )
    assert refused.value.status_code == 400


@pytest.mark.anyio
async def test_enrolment_stores_the_device_key(monkeypatch):
    _, public = _key()
    stored = {}

    async def token_lookup(token_hash):
        return {"id": 1, "company_id": 1}

    async def no_device(uid):
        return None

    async def create_device(**kwargs):
        return {"id": 9, "company_id": 1, "device_uid": kwargs["device_uid"]}

    async def set_key(device_id, key):
        stored[device_id] = key

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(tray_repo, "get_install_token_by_hash", token_lookup)
    monkeypatch.setattr(tray_repo, "get_device_by_uid", no_device)
    monkeypatch.setattr(tray_repo, "create_device", create_device)
    monkeypatch.setattr(tray_repo, "set_device_public_key", set_key)
    monkeypatch.setattr(tray_repo, "mark_install_token_used", nothing)
    monkeypatch.setattr(routes.tray_service, "provision_device_for_checkin", nothing)
    monkeypatch.setattr(routes.rmm_agent_deploy, "upgrade_new_device", nothing)

    await routes.enrol_device(
        TrayEnrolRequest(install_token="a" * 32, device_uid="new-uid", os="windows", public_key=public),
        SimpleNamespace(),
    )
    assert stored == {9: public}

    with pytest.raises(HTTPException) as refused:
        await routes.enrol_device(
            TrayEnrolRequest(install_token="a" * 32, device_uid="new-uid", os="windows", public_key="bad"),
            SimpleNamespace(),
        )
    assert refused.value.status_code == 400


def test_device_list_offers_a_key_reset():
    template = Path("app/templates/admin/tray/_section_devices.html").read_text()
    assert '/admin/tray/devices/{{ device.id }}/reset-key' in template
    assert "{% if device.public_key %}" in template
