import asyncio
from unittest.mock import AsyncMock, patch

from app.core.database import Database


def test_mysql_pool_uses_configured_port():
    database = Database()
    database._use_sqlite = False
    database._settings.database_host = "db.example.test"
    database._settings.database_port = 3307
    database._settings.database_user = "myportal"
    database._settings.database_password = "not-a-real-secret"
    database._settings.database_name = "myportal_test"
    pool = AsyncMock()

    with patch("app.core.database.aiomysql.create_pool", AsyncMock(return_value=pool)) as create_pool:
        asyncio.run(database.connect())

    assert create_pool.await_args.kwargs["port"] == 3307
