"""Per-agent Ed25519 keys for the RMM agent.

The agent makes its own key pair and registers the public key when it enrols
or, for agents enrolled by older releases, once with its token. From then on:

* re-enrolling with the agent's current token also needs a signature from
  that key over the token and the new key, so a token copied off the device
  cannot re-key the agent;
* a live tools socket opens only after the agent signs a fresh challenge.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import rmm as rmm_repo
from app.services import tray_device_key

normalise_public_key = tray_device_key.normalise_public_key


def enrol_proof_message(agent_uid: str, current_token: str, public_key: str) -> bytes:
    token_digest = hashlib.sha256(current_token.encode("utf-8")).hexdigest()
    return (
        "myportal-rmm-enrol-v1\nagent_uid=" + agent_uid + "\ncurrent_token_sha256=" + token_digest
        + "\npublic_key=" + public_key + "\n"
    ).encode("utf-8")


def live_message(agent_uid: str, session_id: int, nonce: str) -> bytes:
    return (
        "myportal-rmm-live-v1\nagent_uid=" + agent_uid + "\nsession_id=" + str(int(session_id))
        + "\nnonce=" + nonce + "\n"
    ).encode("utf-8")


def valid_enrol_proof(
    agent: Mapping[str, Any], current_token: str, public_key: str | None, proof: Any
) -> bool:
    return tray_device_key.verify_signature(
        str(agent.get("public_key") or ""),
        enrol_proof_message(str(agent.get("agent_uid") or ""), current_token, public_key or ""),
        proof,
    )


async def register(agent: Mapping[str, Any], public_key: str) -> str:
    """Register ``agent``'s key: ``registered``, or ``unchanged`` when it
    already holds this key. Raises ``PermissionError`` for a different key."""

    key = normalise_public_key(public_key)
    agent_id = int(agent["id"])
    if agent.get("public_key") == key:
        return "unchanged"
    if not agent.get("public_key") and await rmm_repo.register_agent_public_key(agent_id, key):
        log_info("RMM agent key registered", agent_id=agent_id)
        return "registered"
    current = await rmm_repo.get_agent(agent_id)
    if current and current.get("public_key") == key:
        return "unchanged"
    log_warning("RMM agent tried to replace its registered key", agent_id=agent_id)
    raise PermissionError(
        "A different key is already registered for this RMM agent. Re-enrol the agent to replace it."
    )


async def prove_live_session(websocket: Any, agent: Mapping[str, Any], session_id: int) -> bool:
    """Challenge an accepted live tools socket to sign with the agent's key.
    An agent without a key (an older release) passes unchallenged."""

    agent_uid = str(agent.get("agent_uid") or "")
    return await tray_device_key.challenge_socket(
        websocket,
        str(agent.get("public_key") or ""),
        lambda nonce: live_message(agent_uid, session_id, nonce),
    )
