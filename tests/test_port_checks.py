import pytest

from app.services.port_checks import MAX_PORTS, format_port_spec, parse_port_spec


def test_parses_ports_ranges_and_mixed_separators():
    assert parse_port_spec("443, 8000-8002 22") == [22, 443, 8000, 8001, 8002]


def test_deduplicates_overlapping_ports_and_ranges():
    assert parse_port_spec("80,80,79-81") == [79, 80, 81]


def test_accepts_full_cap():
    assert len(parse_port_spec(f"1-{MAX_PORTS}")) == MAX_PORTS == 1024


@pytest.mark.parametrize(
    "raw", ["", "   ", "0", "65536", "100-50", "80-", "-80", "abc", "1-2000", "80;443"]
)
def test_rejects_invalid_specs(raw):
    with pytest.raises(ValueError) as exc_info:
        parse_port_spec(raw)
    assert str(exc_info.value)


def test_format_compresses_runs_and_round_trips():
    ports = [443, 8000, 8001, 8002, 22]
    assert format_port_spec(ports) == "22,443,8000-8002"
    assert parse_port_spec(format_port_spec(ports)) == sorted(ports)
    assert format_port_spec([]) == ""
