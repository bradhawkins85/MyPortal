package live

import "golang.org/x/sys/windows"

// readDisks lists the fixed drives (not removable, network or optical).
func readDisks() ([]DiskSpace, error) {
	disks := []DiskSpace{}
	for _, root := range fileRoots() {
		path, err := windows.UTF16PtrFromString(root)
		if err != nil || windows.GetDriveType(path) != windows.DRIVE_FIXED {
			continue
		}
		var available, total, free uint64
		if windows.GetDiskFreeSpaceEx(path, &available, &total, &free) != nil || total == 0 {
			continue
		}
		label := make([]uint16, windows.MAX_PATH+1)
		fsName := make([]uint16, windows.MAX_PATH+1)
		if windows.GetVolumeInformation(path, &label[0], uint32(len(label)), nil, nil, nil, &fsName[0], uint32(len(fsName))) != nil {
			label[0], fsName[0] = 0, 0
		}
		disks = append(disks, diskSpace(root, windows.UTF16ToString(label), windows.UTF16ToString(fsName), total, available))
	}
	return disks, nil
}
