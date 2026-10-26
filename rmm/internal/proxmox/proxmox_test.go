package proxmox

import "testing"

func TestParseVersion(t *testing.T) {
	got := ParseVersion("pve-manager/8.2.4/faa83925c9641325 (running kernel: 6.8.12-1-pve)\n")
	if got != "8.2.4" {
		t.Fatalf("ParseVersion = %q", got)
	}
}

func TestParseClusterStatus(t *testing.T) {
	data := []byte(`[{"type":"cluster","name":"lab","quorate":1,"nodes":2,"version":4},
		{"type":"node","name":"pve2","online":0,"local":0},
		{"type":"node","name":"pve1","online":1,"local":1}]`)
	cluster, nodes, err := ParseClusterStatus(data)
	if err != nil {
		t.Fatal(err)
	}
	if cluster == nil || cluster.Name != "lab" || !cluster.Quorate || cluster.Nodes != 2 {
		t.Fatalf("cluster = %+v", cluster)
	}
	if len(nodes) != 2 || nodes[0].Name != "pve1" || !nodes[0].Local || !nodes[0].Online || nodes[1].Online {
		t.Fatalf("nodes = %+v", nodes)
	}
	standalone, nodes, _ := ParseClusterStatus([]byte(`[{"type":"node","name":"pve","online":1,"local":1}]`))
	if standalone != nil || len(nodes) != 1 {
		t.Fatalf("standalone host: cluster %+v nodes %+v", standalone, nodes)
	}
}

func TestParseResources(t *testing.T) {
	data := []byte(`[
		{"type":"qemu","vmid":101,"name":"web","node":"pve1","status":"running","maxcpu":2,"mem":1024,"maxmem":4096,"maxdisk":"34359738368","uptime":60,"template":0},
		{"type":"lxc","vmid":100,"name":"dns","node":"pve2","status":"stopped","maxmem":512},
		{"type":"storage","storage":"local","node":"pve1","plugintype":"dir","status":"available","maxdisk":1000,"disk":250,"shared":0},
		{"type":"storage","storage":"local","node":"pve2","plugintype":"dir","status":"available","maxdisk":1000,"disk":900,"shared":0},
		{"type":"storage","storage":"nfs","node":"pve1","plugintype":"nfs","maxdisk":5000,"disk":10,"shared":1},
		{"type":"storage","storage":"nfs","node":"pve2","plugintype":"nfs","maxdisk":5000,"disk":10,"shared":1},
		{"type":"node","node":"pve1"}]`)
	guests, storage, err := ParseResources(data, "pve1")
	if err != nil {
		t.Fatal(err)
	}
	if len(guests) != 2 || guests[0].VMID != 100 || guests[1].MaxDisk != 34359738368 || guests[1].Status != "running" {
		t.Fatalf("guests = %+v", guests)
	}
	if len(storage) != 2 || storage[0].Name != "local" || storage[0].UsedBytes != 250 || storage[1].Name != "nfs" || !storage[1].Shared {
		t.Fatalf("storage = %+v", storage)
	}
}

func TestParseZpoolList(t *testing.T) {
	pools := ParseZpoolList("rpool\tONLINE\t1000\t400\t600\ntank\tDEGRADED\t2000\t1000\t1000\n")
	if len(pools) != 2 || pools[1].Health != "DEGRADED" || pools[0].FreeBytes != 600 {
		t.Fatalf("pools = %+v", pools)
	}
}

func TestParseTasks(t *testing.T) {
	data := []byte(`[{"starttime":100,"endtime":200,"status":"OK","id":""},
		{"starttime":300,"endtime":400,"status":"job errors","id":"101"},
		{"starttime":500,"status":""}]`)
	backups, err := ParseTasks(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(backups) != 3 || !backups[0].Running || backups[1].OK || backups[1].Guest != "101" || !backups[2].OK {
		t.Fatalf("backups = %+v", backups)
	}
}

func TestParseSubscriptionAndUpdates(t *testing.T) {
	if status, _ := ParseSubscription([]byte(`{"status":"NotFound"}`)); status != "notfound" {
		t.Fatalf("subscription = %q", status)
	}
	if count, _ := ParsePendingUpdates([]byte(`[{"Package":"a"},{"Package":"b"}]`)); count != 2 {
		t.Fatalf("updates = %d", count)
	}
}
