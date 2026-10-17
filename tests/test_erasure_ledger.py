"""Tests for the erasure ledger file (the restore behaviour is in test_anonymisation.py)."""

import json
import stat

import pytest

from app.services import erasure_ledger as ledger

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def path(tmp_path, monkeypatch):
    target = tmp_path / "privacy" / "erasure-ledger.jsonl"
    monkeypatch.setattr(ledger, "ledger_path", lambda: target)
    ledger._invalidate_index()
    return target


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_record_appends_chained_entries_and_skips_duplicates(path):
    assert ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    assert not ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    assert ledger.record_erasure(7, email_hash=HASH_B, request_id=None)
    assert ledger.record_cleared(9, email_hash=HASH_A)

    read = ledger.read_ledger()
    assert read.problems == []
    assert [(e.seq, e.kind, e.user_id, e.email_hash) for e in read.entries] == [
        (1, "erased", 7, HASH_A),
        (2, "erased", 7, HASH_B),
        (3, "cleared", 9, HASH_A),
    ]
    assert read.entries[0].prev == "0" * 64
    assert read.entries[1].prev == read.entries[0].hash


def test_ledger_file_is_private_and_holds_no_addresses(path):
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert set(_lines(path)[0]) == {
        "v", "seq", "kind", "user_id", "email_hash", "request_id", "recorded_at", "prev", "hash"
    }


def test_edited_entry_is_reported_but_still_applied(path):
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    ledger.record_erasure(8, email_hash=HASH_B, request_id=4)
    lines = _lines(path)
    lines[0]["user_id"] = 70
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    read = ledger.read_ledger()
    assert [e.user_id for e in read.entries] == [70, 8]
    assert read.problems == ["line 1: entry was changed after it was written"]


def test_removed_entry_breaks_the_chain(path):
    for user_id in (7, 8, 9):
        ledger.record_erasure(user_id, email_hash=HASH_A, request_id=None)
    kept = path.read_text().splitlines()
    path.write_text(kept[0] + "\n" + kept[2] + "\n")

    read = ledger.read_ledger()
    assert [e.user_id for e in read.entries] == [7, 9]
    assert len(read.problems) == 1 and "chain broken" in read.problems[0]


def test_torn_last_line_is_skipped_and_appending_still_works(path):
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    with path.open("a") as handle:
        handle.write('{"v":1,"seq":2,"kind":"era')

    assert ledger.read_ledger().problems == ["line 2: unreadable entry skipped"]
    assert ledger.record_erasure(8, email_hash=HASH_B, request_id=4)
    read = ledger.read_ledger()
    assert [e.user_id for e in read.entries] == [7, 8]
    assert read.problems == ["line 2: unreadable entry skipped"]


def test_import_merges_a_saved_copy(path, tmp_path):
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    saved = tmp_path / "saved.jsonl"
    saved.write_text(path.read_text())
    ledger.record_erasure(8, email_hash=HASH_B, request_id=4)

    # The live file lost its entries, as on a rebuilt server.
    path.write_text("")
    assert ledger.import_entries(saved) == 1
    assert ledger.import_entries(saved) == 0
    assert [e.user_id for e in ledger.read_ledger().entries] == [7]


def test_missing_ledger_reads_as_empty(path):
    assert ledger.read_ledger().entries == []
    assert ledger._index() == {}


def test_index_ignores_cleared_only_ids_and_sees_new_entries(path):
    ledger.record_cleared(9, email_hash=HASH_A)
    assert ledger._index() == {}
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    assert set(ledger._index()) == {7}
    assert ledger._index()[7].erased == {HASH_A}


@pytest.mark.anyio("asyncio")
async def test_session_check_skips_the_database_for_ids_not_in_the_ledger(path, monkeypatch):
    async def _fail(_ids):
        raise AssertionError("no lookup expected")

    monkeypatch.setattr(ledger.anonymisation_repo, "get_users_by_ids", _fail)
    ledger.record_erasure(7, email_hash=HASH_A, request_id=3)
    assert not await ledger.blocks_session(8)
    assert not await ledger.blocks_session(None)
