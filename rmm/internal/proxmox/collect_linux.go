package proxmox

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const commandTimeout = 30 * time.Second

// Detect reports whether this device is a Proxmox VE host: it has the
// cluster filesystem and the pvesh tool.
func Detect() bool {
	if info, err := os.Stat("/etc/pve"); err != nil || !info.IsDir() {
		return false
	}
	_, err := exec.LookPath("pvesh")
	return err == nil
}

func run(ctx context.Context, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, commandTimeout)
	defer cancel()
	var stderr bytes.Buffer
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		if text := strings.TrimSpace(stderr.String()); text != "" {
			return nil, fmt.Errorf("%s: %w: %s", name, err, text)
		}
		return nil, fmt.Errorf("%s: %w", name, err)
	}
	return out, nil
}

func pvesh(ctx context.Context, path string, args ...string) ([]byte, error) {
	return run(ctx, "pvesh", append([]string{"get", path, "--output-format", "json"}, args...)...)
}

// Collect reads the host's state. full adds what changes slowly and only
// inventory needs: the subscription and pending updates.
func Collect(ctx context.Context, full bool) Status {
	s := Status{Nodes: []Node{}, Guests: []Guest{}, Storage: []Storage{}, ZFSPools: []ZFSPool{},
		Backups: []Backup{}, Errors: []string{}}
	if !Detect() {
		s.Errors = append(s.Errors, "this device is not a Proxmox VE host")
		return s
	}
	if out, err := run(ctx, "pveversion"); err != nil {
		s.fail("version", err)
	} else {
		s.Version = ParseVersion(string(out))
	}

	if out, err := pvesh(ctx, "/cluster/status"); err != nil {
		s.fail("cluster", err)
	} else if cluster, nodes, err := ParseClusterStatus(out); err != nil {
		s.fail("cluster", err)
	} else {
		s.Cluster, s.Nodes = cluster, nodes
		for _, node := range nodes {
			if node.Local {
				s.Node = node.Name
			}
		}
	}
	if s.Node == "" {
		// The node name is the short host name.
		host, _ := os.Hostname()
		s.Node, _, _ = strings.Cut(host, ".")
	}

	if out, err := pvesh(ctx, "/cluster/resources"); err != nil {
		s.fail("guests", err)
	} else if guests, storage, err := ParseResources(out, s.Node); err != nil {
		s.fail("guests", err)
	} else {
		s.Guests, s.Storage = guests, storage
	}

	if _, err := exec.LookPath("zpool"); err == nil {
		if out, err := run(ctx, "zpool", "list", "-H", "-p", "-o", "name,health,size,alloc,free"); err != nil {
			s.fail("zfs", err)
		} else {
			s.ZFSPools = ParseZpoolList(string(out))
		}
	}

	nodePath := "/nodes/" + s.Node
	if out, err := pvesh(ctx, nodePath+"/tasks", "--typefilter", "vzdump", "--limit", fmt.Sprint(MaxBackups)); err != nil {
		s.fail("backups", err)
	} else if backups, err := ParseTasks(out); err != nil {
		s.fail("backups", err)
	} else {
		s.Backups = backups
	}

	if full {
		if out, err := pvesh(ctx, nodePath+"/subscription"); err != nil {
			s.fail("subscription", err)
		} else if status, err := ParseSubscription(out); err != nil {
			s.fail("subscription", err)
		} else {
			s.Subscription = status
		}
		// Reads apt's cached list; it does not refresh it.
		if out, err := pvesh(ctx, nodePath+"/apt/update"); err != nil {
			s.fail("updates", err)
		} else if count, err := ParsePendingUpdates(out); err != nil {
			s.fail("updates", err)
		} else {
			s.PendingUpdates = &count
		}
	}
	return s
}
