package live

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"hash"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	maxListEntries   = 10000
	maxDownloadBytes = 1 << 30
	maxUploadBytes   = 1 << 30
	chunkBytes       = 256 << 10
	downloadWindow   = 8
)

// Entry is one item in a folder listing.
type Entry struct {
	Name     string    `json:"name"`
	Path     string    `json:"path"`
	Type     string    `json:"type"`
	Size     int64     `json:"size"`
	Modified time.Time `json:"modified"`
	Mode     string    `json:"mode"`
	Target   string    `json:"target,omitempty"`
}

type pathArgs struct {
	Path      string `json:"path"`
	Name      string `json:"name"`
	Recursive bool   `json:"recursive"`
}

// cleanPath accepts only absolute paths, so requests never depend on the
// agent's working folder.
func cleanPath(path string) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" || strings.ContainsRune(path, 0) {
		return "", errors.New("enter a full path")
	}
	path = filepath.Clean(path)
	if !filepath.IsAbs(path) {
		return "", fmt.Errorf("%s is not a full path", path)
	}
	return path, nil
}

func isVolumeRoot(path string) bool {
	return filepath.Dir(path) == path
}

func entryFor(dir string, info fs.FileInfo) Entry {
	entry := Entry{
		Name:     info.Name(),
		Path:     filepath.Join(dir, info.Name()),
		Type:     "file",
		Size:     info.Size(),
		Modified: info.ModTime().UTC(),
		Mode:     info.Mode().String(),
	}
	switch {
	case info.Mode()&fs.ModeSymlink != 0:
		entry.Type = "link"
		if target, err := os.Readlink(entry.Path); err == nil {
			entry.Target = target
		}
		if resolved, err := os.Stat(entry.Path); err == nil && resolved.IsDir() {
			entry.Type = "link-dir"
		}
	case info.IsDir():
		entry.Type = "dir"
		entry.Size = 0
	}
	return entry
}

func listDir(path string) (map[string]interface{}, error) {
	dir, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	names, err := dir.Readdirnames(maxListEntries + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	truncated := len(names) > maxListEntries
	if truncated {
		names = names[:maxListEntries]
	}
	entries := make([]Entry, 0, len(names))
	for _, name := range names {
		info, err := os.Lstat(filepath.Join(path, name))
		if err != nil {
			entries = append(entries, Entry{Name: name, Path: filepath.Join(path, name), Type: "unknown"})
			continue
		}
		entries = append(entries, entryFor(path, info))
	}
	sort.Slice(entries, func(i, j int) bool {
		di, dj := strings.HasSuffix(entries[i].Type, "dir"), strings.HasSuffix(entries[j].Type, "dir")
		if di != dj {
			return di
		}
		return strings.ToLower(entries[i].Name) < strings.ToLower(entries[j].Name)
	})
	parent := filepath.Dir(path)
	if parent == path {
		parent = ""
	}
	return map[string]interface{}{"path": path, "parent": parent, "entries": entries, "truncated": truncated}, nil
}

func randomID() string {
	buf := make([]byte, 8)
	_, _ = rand.Read(buf)
	return hex.EncodeToString(buf)
}

// upload is a file being written from the browser. Chunks go to a
// temporary file beside the destination, renamed into place once complete.
type upload struct {
	mu      sync.Mutex
	path    string
	tmp     string
	file    *os.File
	size    int64
	written int64
	seq     int
	hash    hash.Hash
}

func (u *upload) abort() {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.file != nil {
		u.file.Close()
		u.file = nil
	}
	if u.tmp != "" {
		os.Remove(u.tmp)
		u.tmp = ""
	}
}

type uploadArgs struct {
	Path      string `json:"path"`
	Size      int64  `json:"size"`
	Overwrite bool   `json:"overwrite"`
	Upload    string `json:"upload"`
	Seq       int    `json:"seq"`
	Data      string `json:"data"`
	SHA256    string `json:"sha256"`
}

func (s *Session) takeUpload(id string, remove bool) (*upload, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	up, ok := s.uploads[id]
	if !ok {
		return nil, errors.New("this upload is no longer open")
	}
	if remove {
		delete(s.uploads, id)
	}
	return up, nil
}

// download is a file being sent to the browser; it sends at most
// downloadWindow chunks ahead of the browser's acknowledgements.
type download struct {
	credit chan struct{}
	cancel chan struct{}
	once   sync.Once
}

type downloadArgs struct {
	Path     string `json:"path"`
	Transfer string `json:"transfer"`
}

func (s *Session) streamDownload(id string, file *os.File, dl *download) {
	defer file.Close()
	defer func() {
		s.mu.Lock()
		delete(s.downloads, id)
		s.mu.Unlock()
	}()
	digest := sha256.New()
	buf := make([]byte, chunkBytes)
	for seq := 0; ; seq++ {
		select {
		case <-dl.credit:
		case <-dl.cancel:
			return
		case <-s.ctx.Done():
			return
		case <-time.After(2 * time.Minute):
			s.event("fs.chunk", map[string]interface{}{"transfer": id, "seq": seq, "error": "The browser stopped receiving the file."})
			return
		}
		n, err := io.ReadFull(file, buf)
		eof := errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF)
		if err != nil && !eof {
			s.event("fs.chunk", map[string]interface{}{"transfer": id, "seq": seq, "error": err.Error()})
			return
		}
		digest.Write(buf[:n])
		chunk := map[string]interface{}{
			"transfer": id, "seq": seq, "data": base64.StdEncoding.EncodeToString(buf[:n]), "eof": eof,
		}
		if eof {
			chunk["sha256"] = hex.EncodeToString(digest.Sum(nil))
		}
		if !s.event("fs.chunk", chunk) || eof {
			return
		}
	}
}

func init() {
	register("fs.roots", func(s *Session, req Request) (interface{}, error) {
		return map[string]interface{}{"roots": fileRoots()}, nil
	})
	register("fs.list", func(s *Session, req Request) (interface{}, error) {
		var args pathArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		return listDir(path)
	})
	register("fs.mkdir", func(s *Session, req Request) (interface{}, error) {
		var args pathArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		if err := os.Mkdir(path, 0o755); err != nil {
			return nil, err
		}
		return map[string]string{"path": path}, nil
	})
	register("fs.rename", func(s *Session, req Request) (interface{}, error) {
		var args pathArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		name := strings.TrimSpace(args.Name)
		if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\`) || strings.ContainsRune(name, 0) {
			return nil, errors.New("enter a new name without slashes")
		}
		if isVolumeRoot(path) {
			return nil, errors.New("a drive or the root folder cannot be renamed")
		}
		target := filepath.Join(filepath.Dir(path), name)
		if _, err := os.Lstat(target); err == nil {
			return nil, fmt.Errorf("%s already exists", target)
		}
		if err := os.Rename(path, target); err != nil {
			return nil, err
		}
		return map[string]string{"path": target}, nil
	})
	register("fs.delete", func(s *Session, req Request) (interface{}, error) {
		var args pathArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		if isVolumeRoot(path) {
			return nil, errors.New("a drive or the root folder cannot be deleted")
		}
		if args.Recursive {
			err = os.RemoveAll(path)
		} else {
			err = os.Remove(path)
		}
		if err != nil {
			return nil, err
		}
		return map[string]string{"path": path}, nil
	})
	register("fs.download", func(s *Session, req Request) (interface{}, error) {
		var args downloadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		file, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() {
			file.Close()
			if err == nil {
				err = errors.New("only files can be downloaded")
			}
			return nil, err
		}
		if info.Size() > maxDownloadBytes {
			file.Close()
			return nil, fmt.Errorf("files over %d MiB cannot be downloaded here", maxDownloadBytes>>20)
		}
		id := randomID()
		dl := &download{credit: make(chan struct{}, downloadWindow*2), cancel: make(chan struct{})}
		for i := 0; i < downloadWindow; i++ {
			dl.credit <- struct{}{}
		}
		s.mu.Lock()
		s.downloads[id] = dl
		s.mu.Unlock()
		s.reply(req.ID, map[string]interface{}{
			"transfer": id, "name": info.Name(), "size": info.Size(), "chunk_bytes": chunkBytes,
		}, nil)
		go s.streamDownload(id, file, dl)
		return nil, errNoReply
	})
	register("fs.download.ack", func(s *Session, req Request) (interface{}, error) {
		var args downloadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		s.mu.Lock()
		dl := s.downloads[args.Transfer]
		s.mu.Unlock()
		if dl != nil {
			select {
			case dl.credit <- struct{}{}:
			default:
			}
		}
		return nil, errNoReply
	})
	register("fs.download.cancel", func(s *Session, req Request) (interface{}, error) {
		var args downloadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		s.mu.Lock()
		dl := s.downloads[args.Transfer]
		s.mu.Unlock()
		if dl != nil {
			dl.once.Do(func() { close(dl.cancel) })
		}
		return nil, nil
	})
	register("fs.upload.begin", func(s *Session, req Request) (interface{}, error) {
		var args uploadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		if args.Size < 0 || args.Size > maxUploadBytes {
			return nil, fmt.Errorf("files over %d MiB cannot be uploaded here", maxUploadBytes>>20)
		}
		if info, err := os.Lstat(path); err == nil {
			if info.IsDir() {
				return nil, fmt.Errorf("%s is a folder", path)
			}
			if !args.Overwrite {
				return nil, fmt.Errorf("%s already exists", path)
			}
		}
		dir := filepath.Dir(path)
		tmp, err := os.CreateTemp(dir, ".myportal-upload-*")
		if err != nil {
			return nil, err
		}
		id := randomID()
		up := &upload{path: path, tmp: tmp.Name(), file: tmp, size: args.Size, hash: sha256.New()}
		s.mu.Lock()
		s.uploads[id] = up
		s.mu.Unlock()
		return map[string]interface{}{"upload": id, "chunk_bytes": chunkBytes}, nil
	})
	register("fs.upload.chunk", func(s *Session, req Request) (interface{}, error) {
		var args uploadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		up, err := s.takeUpload(args.Upload, false)
		if err != nil {
			return nil, err
		}
		data, err := base64.StdEncoding.DecodeString(args.Data)
		if err != nil {
			return nil, errors.New("the chunk was not valid base64")
		}
		up.mu.Lock()
		defer up.mu.Unlock()
		if up.file == nil {
			return nil, errors.New("this upload is no longer open")
		}
		if args.Seq != up.seq {
			return nil, fmt.Errorf("expected chunk %d, got %d", up.seq, args.Seq)
		}
		if up.written+int64(len(data)) > up.size {
			return nil, errors.New("the upload is larger than announced")
		}
		if _, err := up.file.Write(data); err != nil {
			return nil, err
		}
		up.hash.Write(data)
		up.written += int64(len(data))
		up.seq++
		return map[string]int64{"written": up.written}, nil
	})
	register("fs.upload.end", func(s *Session, req Request) (interface{}, error) {
		var args uploadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		up, err := s.takeUpload(args.Upload, true)
		if err != nil {
			return nil, err
		}
		up.mu.Lock()
		file, tmp := up.file, up.tmp
		up.file = nil
		written, size := up.written, up.size
		digest := hex.EncodeToString(up.hash.Sum(nil))
		up.mu.Unlock()
		defer os.Remove(tmp)
		if file == nil {
			return nil, errors.New("this upload is no longer open")
		}
		if err := file.Close(); err != nil {
			return nil, err
		}
		if written != size {
			return nil, fmt.Errorf("received %d of %d bytes", written, size)
		}
		if args.SHA256 != "" && !strings.EqualFold(args.SHA256, digest) {
			return nil, errors.New("the uploaded file did not match its checksum")
		}
		if err := os.Rename(tmp, up.path); err != nil {
			return nil, err
		}
		return map[string]interface{}{"path": up.path, "size": written, "sha256": digest}, nil
	})
	register("fs.upload.abort", func(s *Session, req Request) (interface{}, error) {
		var args uploadArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if up, err := s.takeUpload(args.Upload, true); err == nil {
			up.abort()
		}
		return nil, nil
	})
}
