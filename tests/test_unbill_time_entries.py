import asyncio

from app.services import unbill_time_entries as module


def test_preview_reads_billable_entries_directly(monkeypatch):
    async def fake_entries(company_id, limit, offset=0):
        assert company_id == 7
        pages = {
            0: [
                {
                    "id": 10,
                    "ticket_id": 100,
                    "ticket_number": "T-100",
                    "subject": "Billed work",
                    "minutes_spent": 60,
                    "labour_type_code": "REMOTE",
                }
            ],
            1: [
                {
                    "id": 11,
                    "ticket_id": 101,
                    "ticket_number": "T-101",
                    "subject": "Closed work",
                    "minutes_spent": 30,
                    "labour_type_name": "Onsite",
                }
            ],
        }
        return pages.get(offset, [])

    monkeypatch.setattr(module.tickets_repo, "list_billable_time_entries", fake_entries)

    preview = asyncio.run(module.preview_unbill_time_entries(7, limit=1))

    assert preview["status"] == "ready"
    assert preview["totals"] == {"timeEntryCount": 2, "minutes": 90}
    assert [item["id"] for item in preview["items"]] == [10, 11]
    assert preview["items"][0]["action"].startswith("Clear the billable flag")


def test_unbill_clears_billed_markers_then_billable_flags(monkeypatch):
    calls = []

    async def fake_preview(company_id, limit=1000):
        return {"status": "ready", "items": [{"id": 10}, {"id": 11}]}

    async def fake_delete(reply_ids):
        calls.append(("delete", list(reply_ids)))
        return 1

    async def fake_mark(reply_ids):
        calls.append(("mark", list(reply_ids)))
        return 2

    monkeypatch.setattr(module, "preview_unbill_time_entries", fake_preview)
    monkeypatch.setattr(module.billed_time_repo, "delete_entries_for_replies", fake_delete)
    monkeypatch.setattr(module.tickets_repo, "mark_replies_non_billable", fake_mark)

    result = asyncio.run(module.unbill_time_entries(7))

    assert calls == [("delete", [10, 11]), ("mark", [10, 11])]
    assert result["status"] == "succeeded"
    assert result["unbilledTimeEntries"] == 2
    assert result["removedBilledTimeMarkers"] == 1
