package checks

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

var envVariable = regexp.MustCompile(`%([A-Za-z0-9_()]+)%`)

// expandPath replaces %NAME% with the environment variable's value; unknown
// names are left as they are.
func expandPath(path string) string {
	return envVariable.ReplaceAllStringFunc(path, func(match string) string {
		if value, ok := os.LookupEnv(match[1 : len(match)-1]); ok {
			return value
		}
		return match
	})
}

// CheckFile reports whether a file or folder exists. A path with * or ? in
// it exists when anything matches.
func CheckFile(path string) client.FileStatus {
	result := client.FileStatus{Path: path}
	if strings.TrimSpace(path) == "" || strings.ContainsAny(path, "\x00\r\n") {
		result.Error = "not a path"
		return result
	}
	resolved := expandPath(path)
	if strings.ContainsAny(resolved, "*?") {
		matches, err := filepath.Glob(resolved)
		if err != nil {
			result.Error = err.Error()
			return result
		}
		if len(matches) == 0 {
			return result
		}
		result.Found = matches[0]
		resolved = matches[0]
	}
	info, err := os.Stat(resolved)
	switch {
	case err == nil:
		result.Exists = true
		if info.IsDir() {
			result.Kind = "folder"
		} else {
			result.Kind = "file"
			size := info.Size()
			result.SizeBytes = &size
		}
		result.ModifiedUTC = info.ModTime().UTC().Format(time.RFC3339)
	case errors.Is(err, fs.ErrNotExist) || errors.Is(err, syscall.ENOTDIR):
	default:
		result.Error = err.Error()
	}
	return result
}
