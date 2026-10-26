// Package proxmox reports the state of a Proxmox VE host: its version,
// cluster quorum and nodes, virtual machines and containers, storage, ZFS
// pools, recent backup jobs, subscription and pending package updates.
//
// The agent runs as root on the host, so it reads everything locally with
// pvesh, pveversion and zpool; no API token is needed. Parsing lives here so it
// can be tested anywhere; running the commands is Linux only
// (collect_linux.go).
package proxmox

import (
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Limits keep one report small enough for a single request.
const (
	MaxGuests  = 1000
	MaxStorage = 100
	MaxPools   = 50
	MaxBackups = 10
	maxText    = 255
)

// Status is everything sent to MyPortal. Errors names the parts that could
// not be read; the rest is still sent.
type Status struct {
	Version        string    `json:"version,omitempty"`
	Node           string    `json:"node,omitempty"`
	Cluster        *Cluster  `json:"cluster,omitempty"`
	Nodes          []Node    `json:"nodes"`
	Guests         []Guest   `json:"guests"`
	Storage        []Storage `json:"storage"`
	ZFSPools       []ZFSPool `json:"zfs_pools"`
	Backups        []Backup  `json:"backups"`
	Subscription   string    `json:"subscription,omitempty"`
	PendingUpdates *int      `json:"pending_updates,omitempty"`
	Errors         []string  `json:"errors"`
}

// Cluster is set when the host is part of a Proxmox VE cluster.
type Cluster struct {
	Name    string `json:"name"`
	Quorate bool   `json:"quorate"`
	Nodes   int    `json:"nodes"`
}

// Node is one cluster member (or the host itself when standalone).
type Node struct {
	Name   string `json:"name"`
	Online bool   `json:"online"`
	Local  bool   `json:"local,omitempty"`
}

// Guest is a virtual machine (qemu) or container (lxc) anywhere in the
// cluster, so a guest that migrates to another node is still found.
type Guest struct {
	VMID      int     `json:"vmid"`
	Name      string  `json:"name,omitempty"`
	Type      string  `json:"type"`
	Node      string  `json:"node,omitempty"`
	Status    string  `json:"status"`
	Template  bool    `json:"template,omitempty"`
	CPUs      float64 `json:"cpus,omitempty"`
	MemBytes  uint64  `json:"mem_bytes,omitempty"`
	MaxMem    uint64  `json:"max_mem_bytes,omitempty"`
	MaxDisk   uint64  `json:"max_disk_bytes,omitempty"`
	UptimeSec int64   `json:"uptime_seconds,omitempty"`
	HAState   string  `json:"ha_state,omitempty"`
}

// Storage is a storage this node can use.
type Storage struct {
	Name       string `json:"name"`
	Type       string `json:"type,omitempty"`
	Status     string `json:"status,omitempty"`
	Shared     bool   `json:"shared,omitempty"`
	Content    string `json:"content,omitempty"`
	TotalBytes uint64 `json:"total_bytes"`
	UsedBytes  uint64 `json:"used_bytes"`
}

// ZFSPool is a pool from zpool list.
type ZFSPool struct {
	Name       string `json:"name"`
	Health     string `json:"health"`
	SizeBytes  uint64 `json:"size_bytes"`
	AllocBytes uint64 `json:"alloc_bytes"`
	FreeBytes  uint64 `json:"free_bytes"`
}

// Backup is a recent vzdump (backup) task on this node, newest first.
type Backup struct {
	StartedAt time.Time  `json:"started_at"`
	EndedAt   *time.Time `json:"ended_at,omitempty"`
	Guest     string     `json:"guest,omitempty"`
	Status    string     `json:"status"`
	OK        bool       `json:"ok"`
	Running   bool       `json:"running,omitempty"`
}

func clean(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > maxText {
		value = value[:maxText]
	}
	return value
}

func (s *Status) fail(part string, err error) {
	if err != nil {
		s.Errors = append(s.Errors, part+": "+clean(err.Error()))
	}
}

// ParseVersion reads the pve-manager version from pveversion's output, such
// as "pve-manager/8.2.4/faa83925c9641325 (running kernel: 6.8.12-1-pve)".
func ParseVersion(output string) string {
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if rest, ok := strings.CutPrefix(line, "pve-manager/"); ok {
			version, _, _ := strings.Cut(rest, "/")
			return clean(version)
		}
	}
	return ""
}

// flexible numbers: pvesh prints most as numbers, some as strings.
type number float64

func (n *number) UnmarshalJSON(data []byte) error {
	text := strings.Trim(string(data), `"`)
	if text == "" || text == "null" {
		*n = 0
		return nil
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil
	}
	*n = number(value)
	return nil
}

func (n number) uint() uint64 {
	if n <= 0 {
		return 0
	}
	return uint64(n)
}

// ParseClusterStatus reads `pvesh get /cluster/status`.
func ParseClusterStatus(data []byte) (*Cluster, []Node, error) {
	var rows []struct {
		Type    string `json:"type"`
		Name    string `json:"name"`
		Online  number `json:"online"`
		Local   number `json:"local"`
		Quorate number `json:"quorate"`
		Nodes   number `json:"nodes"`
	}
	if err := json.Unmarshal(data, &rows); err != nil {
		return nil, nil, fmt.Errorf("cluster status: %w", err)
	}
	var cluster *Cluster
	nodes := []Node{}
	for _, row := range rows {
		switch row.Type {
		case "cluster":
			cluster = &Cluster{Name: clean(row.Name), Quorate: row.Quorate == 1, Nodes: int(row.Nodes)}
		case "node":
			nodes = append(nodes, Node{Name: clean(row.Name), Online: row.Online == 1, Local: row.Local == 1})
		}
	}
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].Name < nodes[j].Name })
	return cluster, nodes, nil
}

// ParseResources reads `pvesh get /cluster/resources`: every guest in the
// cluster, and the storage of node (shared storage once).
func ParseResources(data []byte, node string) ([]Guest, []Storage, error) {
	var rows []struct {
		Type       string `json:"type"`
		VMID       number `json:"vmid"`
		Name       string `json:"name"`
		Node       string `json:"node"`
		Status     string `json:"status"`
		Template   number `json:"template"`
		MaxCPU     number `json:"maxcpu"`
		Mem        number `json:"mem"`
		MaxMem     number `json:"maxmem"`
		Disk       number `json:"disk"`
		MaxDisk    number `json:"maxdisk"`
		Uptime     number `json:"uptime"`
		HAState    string `json:"hastate"`
		Storage    string `json:"storage"`
		PluginType string `json:"plugintype"`
		Shared     number `json:"shared"`
		Content    string `json:"content"`
	}
	if err := json.Unmarshal(data, &rows); err != nil {
		return nil, nil, fmt.Errorf("cluster resources: %w", err)
	}
	guests := []Guest{}
	storage := []Storage{}
	seenShared := map[string]bool{}
	for _, row := range rows {
		switch row.Type {
		case "qemu", "lxc":
			if len(guests) >= MaxGuests {
				continue
			}
			guests = append(guests, Guest{
				VMID: int(row.VMID), Name: clean(row.Name), Type: row.Type, Node: clean(row.Node),
				Status: clean(row.Status), Template: row.Template == 1, CPUs: float64(row.MaxCPU),
				MemBytes: row.Mem.uint(), MaxMem: row.MaxMem.uint(), MaxDisk: row.MaxDisk.uint(),
				UptimeSec: int64(row.Uptime), HAState: clean(row.HAState),
			})
		case "storage":
			if len(storage) >= MaxStorage {
				continue
			}
			shared := row.Shared == 1
			if shared {
				if seenShared[row.Storage] {
					continue
				}
				seenShared[row.Storage] = true
			} else if node != "" && row.Node != node {
				continue
			}
			storage = append(storage, Storage{
				Name: clean(row.Storage), Type: clean(row.PluginType), Status: clean(row.Status), Shared: shared,
				Content: clean(row.Content), TotalBytes: row.MaxDisk.uint(), UsedBytes: row.Disk.uint(),
			})
		}
	}
	sort.Slice(guests, func(i, j int) bool { return guests[i].VMID < guests[j].VMID })
	sort.Slice(storage, func(i, j int) bool { return storage[i].Name < storage[j].Name })
	return guests, storage, nil
}

// ParseZpoolList reads `zpool list -H -p -o name,health,size,alloc,free`.
func ParseZpoolList(output string) []ZFSPool {
	pools := []ZFSPool{}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Split(strings.TrimSpace(line), "\t")
		if len(fields) < 5 || fields[0] == "" {
			continue
		}
		size, _ := strconv.ParseUint(fields[2], 10, 64)
		alloc, _ := strconv.ParseUint(fields[3], 10, 64)
		free, _ := strconv.ParseUint(fields[4], 10, 64)
		pools = append(pools, ZFSPool{Name: clean(fields[0]), Health: clean(fields[1]), SizeBytes: size,
			AllocBytes: alloc, FreeBytes: free})
		if len(pools) >= MaxPools {
			break
		}
	}
	return pools
}

// ParseTasks reads `pvesh get /nodes/{node}/tasks --typefilter vzdump`.
// A finished task's status is "OK" (or "WARNINGS: n") or its error message.
func ParseTasks(data []byte) ([]Backup, error) {
	var rows []struct {
		StartTime number `json:"starttime"`
		EndTime   number `json:"endtime"`
		Status    string `json:"status"`
		ID        string `json:"id"`
	}
	if err := json.Unmarshal(data, &rows); err != nil {
		return nil, fmt.Errorf("backup tasks: %w", err)
	}
	backups := []Backup{}
	for _, row := range rows {
		backup := Backup{StartedAt: time.Unix(int64(row.StartTime), 0).UTC(), Guest: clean(row.ID)}
		if row.EndTime > 0 {
			ended := time.Unix(int64(row.EndTime), 0).UTC()
			backup.EndedAt = &ended
		}
		status := clean(row.Status)
		switch {
		case status == "" || strings.EqualFold(status, "running"):
			backup.Running, backup.Status, backup.OK = true, "running", true
		default:
			backup.Status = status
			backup.OK = status == "OK" || strings.HasPrefix(status, "WARNINGS")
		}
		backups = append(backups, backup)
	}
	sort.Slice(backups, func(i, j int) bool { return backups[i].StartedAt.After(backups[j].StartedAt) })
	if len(backups) > MaxBackups {
		backups = backups[:MaxBackups]
	}
	return backups, nil
}

// ParseSubscription reads `pvesh get /nodes/{node}/subscription`.
func ParseSubscription(data []byte) (string, error) {
	var row struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(data, &row); err != nil {
		return "", fmt.Errorf("subscription: %w", err)
	}
	return clean(strings.ToLower(row.Status)), nil
}

// ParsePendingUpdates counts `pvesh get /nodes/{node}/apt/update`.
func ParsePendingUpdates(data []byte) (int, error) {
	var rows []json.RawMessage
	if err := json.Unmarshal(data, &rows); err != nil {
		return 0, fmt.Errorf("pending updates: %w", err)
	}
	return len(rows), nil
}
