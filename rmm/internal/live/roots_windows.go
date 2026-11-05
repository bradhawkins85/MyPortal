package live

import "golang.org/x/sys/windows"

// fileRoots lists the drive letters in use, such as C:\ and D:\.
func fileRoots() []string {
	mask, err := windows.GetLogicalDrives()
	if err != nil {
		return []string{`C:\`}
	}
	var roots []string
	for i := 0; i < 26; i++ {
		if mask&(1<<uint(i)) != 0 {
			roots = append(roots, string(rune('A'+i))+`:\`)
		}
	}
	return roots
}
