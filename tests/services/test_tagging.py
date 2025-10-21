from app.services.tagging import filter_helpful_slugs, filter_helpful_texts, slugify_tag


def test_filter_helpful_slugs_excludes_unhelpful_examples():
    slugs = [
        "json",
        "network-outage",
        "tag",
        "user-support",
        "patch-required",
    ]
    filtered = filter_helpful_slugs(slugs)

    assert "network-outage" in filtered
    assert "patch-required" in filtered
    assert "json" not in filtered
    assert "tag" not in filtered
    assert "user-support" not in filtered


def test_filter_helpful_texts_normalises_and_filters():
    texts = [" JSON ", "Server Outage", "Normal", "booking appointment", "Patch Work"]

    filtered = filter_helpful_texts(texts)

    assert filtered == ["server outage", "patch work"]
    assert all(slugify_tag(tag) not in {"json", "normal", "booking-appointment"} for tag in filtered)
