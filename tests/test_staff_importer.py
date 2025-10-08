from app.services import staff_importer


def test_find_existing_staff_matches_email():
    existing = [
        {"first_name": "John", "last_name": "Doe", "email": "john@example.com"},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="John",
        last_name="Doe",
        email="john@example.com",
    )
    assert result == existing[0]


def test_find_existing_staff_treats_different_email_as_new():
    existing = [
        {"first_name": "John", "last_name": "Doe", "email": "john@example.com"},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="John",
        last_name="Doe",
        email="john2@example.com",
    )
    assert result is None


def test_find_existing_staff_matches_by_name_when_email_missing():
    existing = [
        {"first_name": "Jane", "last_name": "Smith", "email": None},
    ]
    result = staff_importer._find_existing_staff(  # type: ignore[attr-defined]
        existing,
        first_name="Jane",
        last_name="Smith",
        email=None,
    )
    assert result == existing[0]
