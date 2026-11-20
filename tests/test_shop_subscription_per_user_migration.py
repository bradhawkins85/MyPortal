from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "migrations" / "568_shop_subscription_per_user.sql"


def test_migration_adds_subscription_per_user_idempotently():
    content = MIGRATION.read_text()

    assert "-- phase: expand" in content
    assert "ALTER TABLE shop_products" in content
    assert "ADD COLUMN IF NOT EXISTS subscription_per_user TINYINT(1) NOT NULL DEFAULT 0" in content
