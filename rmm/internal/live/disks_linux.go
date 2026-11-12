package live

import (
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

type mountEntry struct{ device, mount, fsType string }

// Filesystems that sit on a block device but are images or snapshots rather
// than disks someone fills.
var skippedDiskFS = map[string]bool{"squashfs": true, "iso9660": true, "udf": true, "erofs": true}

// parseMounts keeps block-device filesystems from /proc/self/mounts, once
// per device so bind mounts and btrfs subvolumes are not listed twice.
func parseMounts(text string) []mountEntry {
	var out []mountEntry
	seen := map[string]bool{}
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		device, mount, fsType := unescapeMount(fields[0]), unescapeMount(fields[1]), fields[2]
		if !strings.HasPrefix(device, "/dev/") || strings.HasPrefix(device, "/dev/loop") || skippedDiskFS[fsType] {
			continue
		}
		if seen[device] {
			continue
		}
		seen[device] = true
		out = append(out, mountEntry{device: device, mount: mount, fsType: fsType})
	}
	return out
}

// unescapeMount decodes the octal escapes (\040 for a space) the kernel uses.
func unescapeMount(value string) string {
	if !strings.Contains(value, `\`) {
		return value
	}
	var b strings.Builder
	for i := 0; i < len(value); i++ {
		if value[i] == '\\' && i+4 <= len(value) {
			if code, err := strconv.ParseUint(value[i+1:i+4], 8, 8); err == nil {
				b.WriteByte(byte(code))
				i += 3
				continue
			}
		}
		b.WriteByte(value[i])
	}
	return b.String()
}

func readDisks() ([]DiskSpace, error) {
	data, err := os.ReadFile("/proc/self/mounts")
	if err != nil {
		return nil, err
	}
	disks := []DiskSpace{}
	for _, entry := range parseMounts(string(data)) {
		var stat unix.Statfs_t
		if unix.Statfs(entry.mount, &stat) != nil || stat.Blocks == 0 {
			continue
		}
		size := uint64(stat.Frsize)
		if size == 0 {
			size = uint64(stat.Bsize)
		}
		disks = append(disks, diskSpace(entry.mount, "", entry.fsType, stat.Blocks*size, stat.Bavail*size))
	}
	return disks, nil
}
