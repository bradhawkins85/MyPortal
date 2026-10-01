from app.repositories import access_activity as repo


def test_extract_ip_from_webhook_event_prefers_metadata_source_ip():
    row = {
        "metadata": {"source_ip": "203.0.113.7"},
        "headers": {"X-Forwarded-For": "198.51.100.10"},
    }

    assert repo._extract_ip_from_webhook_event(row) == "203.0.113.7"


def test_extract_ip_from_webhook_event_reads_forwarded_headers():
    row = {
        "headers": {
            "X-Forwarded-For": "198.51.100.23, 10.0.0.3",
            "X-Real-IP": "198.51.100.20",
        }
    }

    assert repo._extract_ip_from_webhook_event(row) == "198.51.100.23"
