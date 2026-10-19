"""Exercise image inserts through the real MySQL parameter-escaping path."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

from aiomysql.connection import Connection
from aiomysql.cursors import Cursor

from app.core.database import Database
from app.repositories import marketing_assistant as repository


def test_marketing_image_insert_escapes_binary_parameters(monkeypatch):
    async def run():
        database = Database()
        database._use_sqlite = False
        connection = Connection(charset="utf8mb4", loop=asyncio.get_running_loop())
        connection.server_status = 0
        cursor = Cursor(connection)
        sent = []

        async def send_query(query):
            # Stop at the wire boundary; keep Cursor.execute, _escape_args and
            # Connection.escape real so incompatible driver releases fail here.
            sent.append(query)
            cursor._lastrowid = 41

        @asynccontextmanager
        async def acquire():
            yield SimpleNamespace(cursor=lambda: cursor)

        async def find_image(page_id, sha256):
            return None

        monkeypatch.setattr(database, "acquire", acquire)
        monkeypatch.setattr(cursor, "_query", send_query)
        monkeypatch.setattr(repository, "db", database)
        monkeypatch.setattr(repository, "find_image", find_image)
        data = bytes([137, 80, 78, 71, 0, 255, 39, 92])

        image_id = await repository.store_image(
            page_id=7,
            sha256=hashlib.sha256(data).hexdigest(),
            source_page_url="https://vendor.example/product",
            image_url="https://vendor.example/product.png",
            content_type="image/png",
            data=data,
        )

        assert image_id == 41
        assert len(sent) == 1
        assert sent[0].startswith("INSERT INTO marketing_assistant_images")
        assert "_binary'" in sent[0]
        assert chr(0) not in sent[0]
        assert "\\0" in sent[0]
        assert "\\'" in sent[0]

    asyncio.run(run())
