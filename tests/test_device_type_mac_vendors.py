from app.features.assets.routes import _parse_mac_vendors


def test_parse_mac_vendors_keeps_commas_in_vendor_names():
    value = "Brother Industries\r\nBrother Industries, LTD.\nCANON INC.\n"
    assert _parse_mac_vendors(value) == [
        "Brother Industries",
        "Brother Industries, LTD.",
        "CANON INC.",
    ]


def test_parse_mac_vendors_dedupes_case_insensitively_and_normalises_spaces():
    value = "  xerox   corporation \nXEROX CORPORATION\n\n"
    assert _parse_mac_vendors(value) == ["xerox corporation"]
