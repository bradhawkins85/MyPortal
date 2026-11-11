//go:build windows

package minidump

import (
	"os"
	"path/filepath"
)

// Dir is the folder Windows writes small memory dumps to.
func Dir() string {
	root := os.Getenv("SystemRoot")
	if root == "" {
		root = `C:\Windows`
	}
	return filepath.Join(root, "Minidump")
}
