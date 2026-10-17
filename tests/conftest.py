import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The weak-secret refusal only relaxes when ENVIRONMENT is explicitly
# development/test, so the suite declares itself as a test environment.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "user")
os.environ.setdefault("DB_PASSWORD", "password")
os.environ.setdefault("DB_NAME", "testdb")
# Anonymisation appends to the erasure ledger; keep it out of the checkout.
os.environ.setdefault(
    "PRIVACY_ERASURE_LEDGER_PATH",
    os.path.join(tempfile.mkdtemp(prefix="myportal-ledger-"), "erasure-ledger.jsonl"),
)


async def drain_provision_background_tasks() -> None:
    """Await any pending ``provision_roles_*`` background tasks created by
    :func:`~app.services.m365.provision_app_registration`.

    Call this helper inside the active ``patch.object`` context after
    ``provision_app_registration`` returns so that the background role-grant
    task completes while mocks are still in effect and assertions on Graph
    API call counts are accurate.
    """
    tasks = [
        t for t in asyncio.all_tasks()
        if (t.get_name() or "").startswith("provision_roles_")
        and not t.done()
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture(autouse=True)
def _isolated_erasure_ledger(tmp_path, monkeypatch):
    """Give each test its own erasure ledger once the module is loaded."""
    module = sys.modules.get("app.services.erasure_ledger")
    if module is None:
        yield
        return
    monkeypatch.setattr(module, "ledger_path", lambda: tmp_path / "erasure-ledger.jsonl")
    module._invalidate_index()
    yield
    module._invalidate_index()
