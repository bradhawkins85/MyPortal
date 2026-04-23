import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_USER", "user")
os.environ.setdefault("DB_PASSWORD", "password")
os.environ.setdefault("DB_NAME", "testdb")


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
