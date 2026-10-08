import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.core.database import Database


def test_mysql_pool_uses_configured_port_and_supported_connection_options():
    database = Database()
    database._use_sqlite = False
    database._settings = SimpleNamespace(
        database_host="db.example.test",
        database_port=3307,
        database_user="myportal",
        database_password="not-a-real-secret",
        database_name="myportal_test",
        db_pool_wait_timeout=600,
    )
    connection = Mock()

    async def connect_and_disconnect():
        try:
            await database.connect()
            assert database.is_connected()
            assert database._pool.minsize == 1
            assert database._pool.maxsize == 10
        finally:
            await database.disconnect()

    # Keep the real pool and public connect() argument validation. Mock only
    # the internal connection coroutine so no MySQL server is required.
    with patch("aiomysql.connection._connect", AsyncMock(return_value=connection)) as connect:
        asyncio.run(connect_and_disconnect())

    connect.assert_awaited_once()
    kwargs = connect.await_args.kwargs
    assert kwargs["host"] == "db.example.test"
    assert kwargs["port"] == 3307
    assert kwargs["user"] == "myportal"
    assert kwargs["password"] == "not-a-real-secret"
    assert kwargs["db"] == "myportal_test"
    assert kwargs["autocommit"] is True
    assert kwargs["init_command"] == "SET time_zone = '+00:00'"
    connection.close.assert_called_once()
    assert not database.is_connected()
