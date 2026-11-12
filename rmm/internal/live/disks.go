package live

// DiskSpace is the size and use of one fixed disk volume.
type DiskSpace struct {
	Mount  string `json:"mount"`
	Label  string `json:"label,omitempty"`
	FSType string `json:"fs_type,omitempty"`
	Total  uint64 `json:"total"`
	Used   uint64 `json:"used"`
}

// diskSpace counts reserved blocks as used, so the share matches the space
// the agent account can still write to.
func diskSpace(mount, label, fsType string, total, available uint64) DiskSpace {
	disk := DiskSpace{Mount: mount, Label: label, FSType: fsType, Total: total}
	if available <= total {
		disk.Used = total - available
	}
	return disk
}
