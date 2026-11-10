"""Per-device Ed25519 keys for tray devices.

Each tray service makes its own key pair and registers the public key, at
enrolment or, for devices enrolled by older releases, once with its device
token. From then on the WebSocket at ``/ws/tray/{device_uid}`` only opens
after the device signs a fresh server challenge, so a copied bearer token is
not enough to impersonate the device or receive its commands.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import secrets
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64
CHALLENGE_TIMEOUT_SECONDS = 15.0
_MAX_MESSAGES_BEFORE_AUTH = 10


def normalise_public_key(value: str | None) -> str:
    """The key as canonical base64, or ``ValueError`` when it is not a
    32-byte Ed25519 public key."""

    try:
        raw = base64.b64decode(str(value or "").strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("public_key must be base64") from exc
    if len(raw) != PUBLIC_KEY_BYTES:
        raise ValueError("public_key must be a 32-byte Ed25519 key")
    Ed25519PublicKey.from_public_bytes(raw)
    return base64.b64encode(raw).decode("ascii")


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def challenge_message(device_uid: str, nonce: str) -> bytes:
    return ("myportal-tray-ws-v1\ndevice_uid=" + device_uid + "\nnonce=" + nonce + "\n").encode("utf-8")


def verify_signature(public_key: str, message: bytes, signature: Any) -> bool:
    """Whether ``signature`` (base64) is ``public_key``'s signature of ``message``."""

    try:
        raw_signature = base64.b64decode(str(signature or ""), validate=True)
        if len(raw_signature) != SIGNATURE_BYTES:
            return False
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(raw_signature, message)
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


def verify(public_key: str, device_uid: str, nonce: str, signature: Any) -> bool:
    return verify_signature(public_key, challenge_message(device_uid, nonce), signature)


async def _await_auth(websocket: Any) -> Any:
    for _ in range(_MAX_MESSAGES_BEFORE_AUTH):
        message = await websocket.receive_json()
        if isinstance(message, dict) and message.get("type") == "auth":
            return message.get("signature")
    return None


async def challenge_socket(
    websocket: Any, public_key: str, message_for: Callable[[str], bytes], *, announce: bool = False
) -> bool:
    """Send a fresh nonce and check the signed reply. ``announce`` sends the
    challenge, marked ``"required": false``, even without a key, for a
    protocol where the client reads it first; only a key is then waited for."""

    if not public_key and not announce:
        return True
    nonce = new_nonce()
    challenge: dict[str, Any] = {"type": "challenge", "nonce": nonce}
    if announce:
        challenge["required"] = bool(public_key)
    await websocket.send_json(challenge)
    if not public_key:
        return True
    try:
        signature = await asyncio.wait_for(_await_auth(websocket), CHALLENGE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return False
    except Exception:  # noqa: BLE001 - a disconnect or malformed frame fails the challenge
        return False
    return verify_signature(public_key, message_for(nonce), signature)


async def prove_possession(websocket: Any, device: Mapping[str, Any]) -> bool:
    """Challenge an accepted WebSocket to sign with the device's registered
    key. A device without a key (an older release) passes unchallenged."""

    device_uid = str(device.get("device_uid") or "")
    return await challenge_socket(
        websocket, str(device.get("public_key") or ""), lambda nonce: challenge_message(device_uid, nonce)
    )
