from app.services import tacticalrmm


def test_extract_agent_page_handles_beta_results():
    response = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": 20,
                "hostname": "BJP-LAB-ACQ1",
                "agent_id": "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz",
                "operating_system": "Windows 10 Pro",
            }
        ],
    }

    items, next_endpoint = tacticalrmm._extract_agent_page(response, "https://example.com")

    assert len(items) == 1
    assert items[0]["hostname"] == "BJP-LAB-ACQ1"
    assert next_endpoint is None


def test_extract_agent_details_handles_beta_agent_payload():
    agent = {
        "id": 20,
        "hostname": "BJP-LAB-ACQ1",
        "agent_id": "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz",
        "operating_system": "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)",
        "last_seen": "2025-11-04T02:40:20.840228Z",
        "plat": "windows",
        "services": [],
    }

    details = tacticalrmm.extract_agent_details(agent)

    assert details["name"] == "BJP-LAB-ACQ1"
    assert details["os_name"] == "Windows 10 Pro, 64 bit v22H2 (build 19045.6456)"
    assert details["tactical_asset_id"] == "OEBIMTvujlppgOaNxYerEVowqDstjsGeNKCsnSSz"
    assert details["last_sync"] == "2025-11-04T02:40:20.840228Z"
