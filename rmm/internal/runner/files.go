package runner

import (
	"context"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

var fileRunSlot = make(chan struct{}, 1)

func validDigest(value string, size int) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == size
}

func fileMatches(path string, expected client.ScriptFile) (bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return false, err
	}
	if !info.Mode().IsRegular() {
		return false, errors.New("destination is not a regular file")
	}
	if info.Size() != expected.Size {
		return false, nil
	}
	m, s := md5.New(), sha256.New()
	count, err := io.Copy(io.MultiWriter(m, s), f)
	if err != nil {
		return false, err
	}
	return count == expected.Size && strings.EqualFold(hex.EncodeToString(m.Sum(nil)), expected.MD5) &&
		strings.EqualFold(hex.EncodeToString(s.Sum(nil)), expected.SHA256), nil
}

type boundedFileWriter struct {
	out       io.Writer
	remaining int64
}

func (w *boundedFileWriter) Write(data []byte) (int, error) {
	if int64(len(data)) > w.remaining {
		return 0, errors.New("download exceeds expected file size")
	}
	n, err := w.out.Write(data)
	w.remaining -= int64(n)
	return n, err
}

func (r *Runner) prepareFiles(ctx context.Context, job client.Job) (string, error) {
	if len(job.Files) > 20 {
		return "", errors.New("too many associated files")
	}
	seen := map[string]bool{}
	for _, file := range job.Files {
		path := filepath.Clean(file.Destination)
		if !filepath.IsAbs(path) || path == filepath.VolumeName(path)+string(os.PathSeparator) ||
			!validDigest(file.MD5, md5.Size) || !validDigest(file.SHA256, sha256.Size) || file.Size < 0 || file.Size > 50<<20 {
			return "", fmt.Errorf("invalid file manifest for %s", file.SourcePath)
		}
		key := path
		if os.PathSeparator == '\\' {
			key = strings.ToLower(key)
		}
		if seen[key] {
			return "", errors.New("duplicate destination path")
		}
		seen[key] = true
	}
	var messages strings.Builder
	for index, file := range job.Files {
		if err := ctx.Err(); err != nil {
			return messages.String(), err
		}
		reused, err := r.prepareFile(ctx, job.ID, index, file)
		if err != nil {
			return messages.String(), fmt.Errorf("%s → %s: %w", file.SourcePath, file.Destination, err)
		}
		action := "downloaded and verified"
		if reused {
			action = "already matches; download skipped"
		}
		fmt.Fprintf(&messages, "File %s: %s\n", file.Destination, action)
	}
	return messages.String(), nil
}

func (r *Runner) prepareFile(ctx context.Context, runID int64, index int, file client.ScriptFile) (bool, error) {
	path := filepath.Clean(file.Destination)
	mode := os.FileMode(0o700)
	info, err := os.Lstat(path)
	if err == nil {
		if !info.Mode().IsRegular() {
			return false, errors.New("destination is not a regular file")
		}
		mode = info.Mode().Perm()
		matches, err := fileMatches(path, file)
		if err != nil {
			return false, err
		}
		if matches {
			return true, nil
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return false, err
	}
	if r.DownloadFile == nil {
		return false, errors.New("file downloads are unavailable")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return false, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".myportal-download-*")
	if err != nil {
		return false, err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	writer := &boundedFileWriter{out: tmp, remaining: file.Size}
	if err := r.DownloadFile(ctx, runID, index, writer); err != nil {
		return false, err
	}
	if writer.remaining != 0 {
		return false, errors.New("download was incomplete")
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if err := tmp.Chmod(mode); err != nil {
		return false, err
	}
	if err := tmp.Sync(); err != nil {
		return false, err
	}
	if err := tmp.Close(); err != nil {
		return false, err
	}
	matches, err := fileMatches(tmp.Name(), file)
	if err != nil {
		return false, err
	}
	if !matches {
		return false, errors.New("download failed checksum verification")
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return false, err
	}
	// Verify the final destination on disk before the script can use it.
	matches, err = fileMatches(path, file)
	if err != nil {
		return false, err
	}
	if !matches {
		return false, errors.New("destination failed checksum verification")
	}
	return false, nil
}
