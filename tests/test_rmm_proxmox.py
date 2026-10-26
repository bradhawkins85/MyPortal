from app.services import rmm_checks, rmm_inventory, rmm_proxmox

STATUS = {
    "version": "8.2.4",
    "node": "pve1",
    "cluster": {"name": "lab", "quorate": True, "nodes": 2},
    "nodes": [{"name": "pve1", "online": True, "local": True}, {"name": "pve2", "online": False}],
    "guests": [
        {"vmid": 101, "name": "web", "type": "qemu", "node": "pve1", "status": "running"},
        {"vmid": 102, "name": "db", "type": "lxc", "node": "pve2", "status": "stopped"},
        {"vmid": 9000, "name": "tpl", "type": "qemu", "status": "stopped", "template": True},
    ],
    "storage": [
        {"name": "local", "total_bytes": 100, "used_bytes": 50},
        {"name": "local-zfs", "total_bytes": 100, "used_bytes": 95},
    ],
    "zfs_pools": [{"name": "rpool", "health": "ONLINE"}, {"name": "tank", "health": "DEGRADED"}],
    "backups": [
        {"started_at": "2026-10-26T01:00:00Z", "status": "", "running": True, "ok": True},
        {"started_at": "2026-10-25T01:00:00Z", "status": "job errors", "ok": False},
    ],
    "errors": [],
}


def _check(condition, target="", **settings):
    return {"check_type": "proxmox", "target": target, "settings": {"condition": condition, **settings}}


def _evaluate(condition, target="", **settings):
    return rmm_checks.evaluate(_check(condition, target, **settings), {"proxmox": STATUS})


def test_guest_condition_follows_vmid_or_name_across_nodes():
    assert _evaluate("guest", "101") == (False, "Running: 101 (web) on pve1")
    problem, text = _evaluate("guest", "db")
    assert problem and text.startswith("Not running: 102 (db) is stopped on pve2")
    assert _evaluate("guest", "9000")[0] is True  # templates never run
    assert _evaluate("guest", "404") == (True, "Not found: no virtual machine or container 404")


def test_storage_zfs_backup_quorum_and_node_conditions():
    assert _evaluate("storage", "*", threshold=90) == (True, "local-zfs 95% full (0 of 0 GB)")
    assert _evaluate("storage", "local", threshold=90)[0] is False
    assert _evaluate("zfs") == (True, "tank DEGRADED")
    assert _evaluate("zfs", "rpool") == (False, "rpool ONLINE")
    problem, text = _evaluate("backup")
    assert problem and "job errors" in text  # the running job is skipped
    assert _evaluate("quorum")[0] is False
    assert _evaluate("node_offline") == (True, "Offline: pve2")
    assert _evaluate("node_offline", "pve1") == (False, "1 node online")


def test_non_proxmox_hosts_and_older_agents():
    report = {"proxmox": {"errors": ["this device is not a Proxmox VE host"]}}
    assert rmm_checks.evaluate(_check("quorum"), report) == (True, "Not a Proxmox VE host")
    assert rmm_checks.evaluate(_check("quorum"), {}) is None


def test_conditions_on_the_same_name_do_not_replace_each_other():
    assert rmm_checks.override_key(_check("guest", "local")) != rmm_checks.override_key(_check("storage", "local"))
    assert rmm_checks.override_key(_check("storage", "Local")) == rmm_checks.override_key(_check("storage", "local"))


def test_parse_settings_and_agent_config():
    target, settings, _, errors = rmm_checks.parse_settings(
        "proxmox", {"proxmox_condition": "storage", "proxmox_target": "local-zfs", "proxmox_threshold": "80"})
    assert not errors and target == "local-zfs" and settings == {"condition": "storage", "threshold": 80.0}
    target, settings, _, errors = rmm_checks.parse_settings(
        "proxmox", {"proxmox_condition": "quorum", "proxmox_target": "ignored"})
    assert not errors and target == "" and settings == {"condition": "quorum"}
    _, _, _, errors = rmm_checks.parse_settings("proxmox", {"proxmox_condition": "guest", "proxmox_target": ""})
    assert "proxmox_target" in errors
    _, _, _, errors = rmm_checks.parse_settings("proxmox", {"proxmox_condition": "nope"})
    assert "proxmox_condition" in errors
    assert rmm_checks.agent_config_for([_check("guest", "101")])["proxmox"] is True
    assert rmm_checks.agent_config_for([{"check_type": "cpu", "target": ""}])["proxmox"] is False
    assert rmm_checks.describe({**_check("storage", "local", threshold=80), "duration_minutes": 5}) == (
        "Storage local is over 80% full for 5 minutes")


def test_inventory_keeps_a_clean_proxmox_section():
    noisy = {**STATUS, "guests": [{"vmid": "101", "name": "x" * 400, "status": "RUNNING", "type": "bogus"}] * 2000}
    _, details, _ = rmm_inventory.normalise({"proxmox": noisy})
    guests = details["proxmox"]["guests"]
    assert len(guests) == rmm_proxmox.MAX_GUESTS
    assert guests[0] == {**guests[0], "vmid": 101, "status": "running", "type": "qemu"}
    assert len(guests[0]["name"]) == 255
    _, details, _ = rmm_inventory.normalise({})
    assert "proxmox" not in details
    assert rmm_proxmox.format_uptime(90061) == "1d 1h"
