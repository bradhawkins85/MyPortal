package live

import (
	"strings"

	"golang.org/x/sys/unix"
)

// readDisks lists local volumes a person can browse. The APFS system
// volumes under /System/Volumes share the root's container, so "/" stands
// for them; read-only and removable volumes (disk images, USB sticks) are
// not fixed disks.
func readDisks() ([]DiskSpace, error) {
	count, err := unix.Getfsstat(nil, unix.MNT_NOWAIT)
	if err != nil {
		return nil, err
	}
	buf := make([]unix.Statfs_t, count)
	count, err = unix.Getfsstat(buf, unix.MNT_NOWAIT)
	if err != nil {
		return nil, err
	}
	disks := []DiskSpace{}
	for _, stat := range buf[:count] {
		mount := unix.ByteSliceToString(stat.Mntonname[:])
		fsType := unix.ByteSliceToString(stat.Fstypename[:])
		if stat.Flags&unix.MNT_LOCAL == 0 || stat.Flags&unix.MNT_REMOVABLE != 0 || stat.Blocks == 0 {
			continue
		}
		if mount != "/" && (stat.Flags&(unix.MNT_DONTBROWSE|unix.MNT_RDONLY) != 0 || strings.HasPrefix(mount, "/System/Volumes/")) {
			continue
		}
		if fsType == "devfs" || fsType == "autofs" || fsType == "nullfs" {
			continue
		}
		size := uint64(stat.Bsize)
		disks = append(disks, diskSpace(mount, "", fsType, stat.Blocks*size, stat.Bavail*size))
	}
	return disks, nil
}
