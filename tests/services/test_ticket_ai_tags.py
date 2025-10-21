from app.services import tickets


def test_finalise_tags_filters_unhelpful_slugs():
    raw_tags = ["json", "printer-error", "tags", "normal"]

    filtered = tickets._finalise_tags(raw_tags, {})

    assert "printer-error" in filtered
    assert "json" not in filtered
    assert "tags" not in filtered
    assert "normal" not in filtered

    # Ensure the helper still pads the list to at least five entries using defaults.
    assert len(filtered) >= 5
