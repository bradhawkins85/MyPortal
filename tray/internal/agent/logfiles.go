package agent

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// FileSourcePrefix marks a log request that names a log file (or a glob of
// log files) rather than an event channel, e.g.
// `file:C:\Windows\Logs\CBS\CBS.log` or `file:/Library/Logs/*.log`. The path
// must sit under one of the platform's allowlisted log folders and the file
// must have a log-like extension, so a request can never read an arbitrary
// file. The server applies the same rules before sending a request.
const FileSourcePrefix = "file:"

// Bounds on what one job reads from log files.
const (
	maxFilesPerRequest = 10
	maxFileTailBytes   = 1 << 20
	maxFileLogBytes    = 6 << 20
	maxGlobCandidates  = 2000
	maxFilePatternLen  = 400
)

// WindowsLogFileRoots are the folders log files may be read from on Windows.
// A "*" segment matches any one folder (each user profile).
// Keep in sync with WINDOWS_LOG_FILE_ROOTS in app/services/ai_troubleshooter.py.
var WindowsLogFileRoots = []string{
	`C:\Windows\Logs`,
	`C:\Windows\Panther`,
	`C:\Windows\debug`,
	`C:\Windows\INF`,
	`C:\Windows\Temp`,
	`C:\Windows\CCM\Logs`,
	`C:\Windows\System32\LogFiles`,
	`C:\Windows\SoftwareDistribution`,
	`C:\ProgramData\Microsoft\IntuneManagementExtension\Logs`,
	`C:\ProgramData\Microsoft\Windows Defender\Support`,
	`C:\ProgramData\Microsoft\Windows\WER\ReportArchive`,
	`C:\ProgramData\Microsoft\Windows\WER\ReportQueue`,
	`C:\Users\*\AppData\Local\Temp`,
	`C:\Users\*\AppData\Roaming\Microsoft\Teams`,
	`C:\Users\*\AppData\Local\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\Logs`,
}

// MacOSLogFileRoots are the folders log files may be read from on macOS.
// /var is a symlink to /private/var, so both spellings are listed.
// Keep in sync with MACOS_LOG_FILE_ROOTS in app/services/ai_troubleshooter.py.
var MacOSLogFileRoots = []string{
	"/var/log",
	"/private/var/log",
	"/Library/Logs",
	"/Users/*/Library/Logs",
}

// logFileNameRe is the allowlist of log file extensions (plain-text logs,
// Windows Error Reporting reports, rotated ConfigMgr logs and macOS crash
// reports, optionally with a rotation number).
var logFileNameRe = regexp.MustCompile(`(?i)\.(log|txt|wer|lo_|ips|crash|panic|diag)(\.[0-9]{1,3})?$`)

// pathStyle describes how a platform spells paths, so the checks can be
// tested for either platform on any build.
type pathStyle struct {
	sep  string
	fold bool // case-insensitive comparison (Windows)
}

var (
	windowsPaths = pathStyle{sep: `\`, fold: true}
	posixPaths   = pathStyle{sep: "/", fold: false}
)

func (s pathStyle) equal(a, b string) bool {
	if s.fold {
		return strings.EqualFold(a, b)
	}
	return a == b
}

func hasWildcard(seg string) bool { return strings.ContainsAny(seg, "*?") }

// splitPath validates an absolute path or pattern and returns its segments.
// The first segment is the drive ("C:") on Windows and "" on POSIX.
func (s pathStyle) splitPath(p string) ([]string, error) {
	if p == "" || len(p) > maxFilePatternLen {
		return nil, errors.New("path is empty or too long")
	}
	if strings.ContainsAny(p, "[]\"<>|\x00") || strings.ContainsFunc(p, func(r rune) bool { return r < 0x20 }) {
		return nil, errors.New("path contains a character that is not allowed")
	}
	if s.sep == `\` {
		p = strings.ReplaceAll(p, "/", `\`)
		if len(p) < 3 || p[1] != ':' || p[2] != '\\' || !isLetter(p[0]) {
			return nil, errors.New(`path must start with a drive, e.g. C:\`)
		}
		if strings.Contains(p[2:], ":") {
			return nil, errors.New("path may not name a stream or second drive")
		}
	} else if !strings.HasPrefix(p, "/") {
		return nil, errors.New("path must be absolute")
	}
	raw := strings.Split(p, s.sep)
	segs := make([]string, 0, len(raw))
	for i, seg := range raw {
		if seg == "" && i > 0 {
			continue // doubled or trailing separator
		}
		if seg == "." || seg == ".." {
			return nil, errors.New("path may not contain . or .. segments")
		}
		if s.sep == `\` && i > 0 && strings.TrimRight(seg, ". ") != seg {
			return nil, errors.New("path segments may not end in a dot or space")
		}
		segs = append(segs, seg)
	}
	return segs, nil
}

func isLetter(b byte) bool { return (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') }

// underRoot reports whether segs lies strictly inside root. Wildcards in segs
// are only accepted where the root itself has a "*" segment or below the root,
// so a pattern can never widen which folder it reads from.
func (s pathStyle) underRoot(segs []string, root string) bool {
	rootSegs, err := s.splitPath(root)
	if err != nil || len(segs) <= len(rootSegs) {
		return false
	}
	for i, r := range rootSegs {
		if r == "*" {
			continue
		}
		if hasWildcard(segs[i]) || !s.equal(segs[i], r) {
			return false
		}
	}
	return true
}

// allowedFilePattern validates a requested file pattern against roots and
// returns its segments.
func (s pathStyle) allowedFilePattern(pattern string, roots []string) ([]string, error) {
	segs, err := s.splitPath(pattern)
	if err != nil {
		return nil, err
	}
	if !logFileNameRe.MatchString(segs[len(segs)-1]) && !hasWildcard(segs[len(segs)-1]) {
		return nil, errors.New("not a log file (allowed: .log, .txt, .wer, .lo_, .ips, .crash, .panic, .diag)")
	}
	for _, root := range roots {
		if s.underRoot(segs, root) {
			return segs, nil
		}
	}
	return nil, errors.New("not inside an allowlisted log folder")
}

// allowedFile reports whether a concrete, resolved path may be read.
func (s pathStyle) allowedFile(path string, roots []string) bool {
	segs, err := s.splitPath(path)
	if err != nil || !logFileNameRe.MatchString(segs[len(segs)-1]) {
		return false
	}
	for _, seg := range segs {
		if hasWildcard(seg) {
			return false
		}
	}
	for _, root := range roots {
		if s.underRoot(segs, root) {
			return true
		}
	}
	return false
}

// isFileLogSource reports whether source is a file request this platform
// may serve.
func isFileLogSource(source string) bool {
	pattern, ok := strings.CutPrefix(source, FileSourcePrefix)
	if !ok || len(platformFileRoots) == 0 {
		return false
	}
	_, err := platformPathStyle.allowedFilePattern(strings.TrimSpace(pattern), platformFileRoots)
	return err == nil
}

// matchSegment matches one path segment against a pattern segment.
func (s pathStyle) matchSegment(pattern, name string) bool {
	if s.fold {
		pattern, name = strings.ToLower(pattern), strings.ToLower(name)
	}
	ok, err := filepath.Match(pattern, name)
	return err == nil && ok
}

// expand resolves a validated pattern into existing paths. Matching is
// case-insensitive on Windows, where filepath.Glob is not.
func (s pathStyle) expand(segs []string) []string {
	base := segs[0] + s.sep
	paths := []string{base}
	for i, seg := range segs[1:] {
		last := i == len(segs)-2
		var next []string
		for _, dir := range paths {
			if !hasWildcard(seg) {
				next = append(next, joinPath(s, dir, seg))
				continue
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, e := range entries {
				if !last && !e.IsDir() {
					continue
				}
				if s.matchSegment(seg, e.Name()) {
					next = append(next, joinPath(s, dir, e.Name()))
				}
				if len(next) >= maxGlobCandidates {
					break
				}
			}
		}
		paths = next
		if len(paths) == 0 {
			return nil
		}
	}
	return paths
}

func joinPath(s pathStyle, dir, name string) string {
	if strings.HasSuffix(dir, s.sep) {
		return dir + name
	}
	return dir + s.sep + name
}

type logFile struct {
	path    string
	size    int64
	modTime time.Time
}

// findLogFiles expands a validated pattern and keeps the regular, allowlisted
// log files, newest first. Symlinks are resolved and the target re-checked.
func (s pathStyle) findLogFiles(segs []string, roots []string) []logFile {
	var files []logFile
	seen := map[string]bool{}
	for _, candidate := range s.expand(segs) {
		resolved, err := filepath.EvalSymlinks(candidate)
		if err != nil || seen[resolved] || !s.allowedFile(resolved, roots) {
			continue
		}
		info, err := os.Stat(resolved)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		seen[resolved] = true
		files = append(files, logFile{path: resolved, size: info.Size(), modTime: info.ModTime()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].modTime.After(files[j].modTime) })
	return files
}

// collectFileLogs reads the requested log files, one "### file:<path> Log"
// section per file so each lands as its own file in the bundle. Only the tail
// of a large file is read.
func collectFileLogs(ctx context.Context, specs []LogSpec, roots []string, style pathStyle, now time.Time) (string, []string) {
	var b strings.Builder
	var sources []string
	budget := maxFileLogBytes
	for _, spec := range specs {
		pattern := strings.TrimSpace(strings.TrimPrefix(spec.Source, FileSourcePrefix))
		segs, err := style.allowedFilePattern(pattern, roots)
		if err != nil {
			fmt.Fprintf(&b, "### %s Log\n[refused: %s]\n\n", spec.Source, err)
			continue
		}
		files := style.findLogFiles(segs, roots)
		wildcard := strings.ContainsAny(pattern, "*?")
		if wildcard {
			// A glob reads only files written in the window; a named file
			// is read whatever its age, since the article asked for it.
			cutoff := now.Add(-spec.Window)
			recent := files[:0]
			for _, f := range files {
				if !f.modTime.Before(cutoff) {
					recent = append(recent, f)
				}
			}
			if len(recent) == 0 && len(files) > 0 {
				fmt.Fprintf(&b, "### %s Log\n[no matching log files changed in the last %s; %d older file(s) skipped]\n\n",
					spec.Source, spec.Window, len(files))
				continue
			}
			files = recent
		}
		if len(files) == 0 {
			fmt.Fprintf(&b, "### %s Log\n[no matching log files found]\n\n", spec.Source)
			continue
		}
		skipped := 0
		if len(files) > maxFilesPerRequest {
			skipped = len(files) - maxFilesPerRequest
			files = files[:maxFilesPerRequest]
		}
		for _, f := range files {
			if ctx.Err() != nil {
				return b.String(), sources
			}
			source := FileSourcePrefix + f.path
			if budget <= 0 {
				fmt.Fprintf(&b, "### %s Log\n[skipped: the job's log file budget is used up]\n\n", source)
				continue
			}
			limit := maxFileTailBytes
			if budget < limit {
				limit = budget
			}
			text, read, err := readLogTail(f.path, f.size, int64(limit))
			if err != nil {
				fmt.Fprintf(&b, "### %s Log\n[unavailable: %s]\n\n", source, err)
				continue
			}
			budget -= int(read)
			header := fmt.Sprintf("[file modified %s, %d bytes", f.modTime.UTC().Format(time.RFC3339), f.size)
			if read < f.size {
				header += fmt.Sprintf("; showing the last %d bytes", read)
			}
			header += "]"
			if strings.TrimSpace(text) == "" {
				text = "[file is empty]"
			}
			fmt.Fprintf(&b, "### %s Log\n%s\n%s\n\n", source, header, strings.TrimRight(text, "\r\n"))
			sources = append(sources, source)
		}
		if skipped > 0 {
			fmt.Fprintf(&b, "### %s Log\n[%d more matching file(s) not read; only the newest %d are collected]\n\n",
				spec.Source, skipped, maxFilesPerRequest)
		}
	}
	return b.String(), sources
}

// readLogTail reads up to limit bytes from the end of a text log file and
// decodes it (UTF-8 or UTF-16). Binary files are refused.
func readLogTail(path string, size, limit int64) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	var bom [2]byte
	n, _ := io.ReadFull(f, bom[:])
	utf16BOM := n == 2 && bom[0] == 0xFF && bom[1] == 0xFE
	offset := int64(0)
	if size > limit {
		offset = size - limit
		if offset%2 != 0 {
			offset++ // keep UTF-16 code units aligned
		}
	}
	if utf16BOM && offset < 2 {
		offset = 2
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		return "", 0, err
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit))
	if err != nil {
		return "", 0, err
	}
	var text string
	if utf16BOM {
		text = normaliseText(append([]byte{0xFF, 0xFE}, raw...))
	} else {
		probe := raw
		if len(probe) > 8192 {
			probe = probe[:8192]
		}
		if bytes.IndexByte(probe, 0) >= 0 && !looksUTF16LE(probe) {
			return "", 0, errors.New("not a text file")
		}
		text = normaliseText(raw)
	}
	if offset > 2 {
		// Drop the partial first line of a tail read.
		if i := strings.IndexByte(text, '\n'); i >= 0 {
			text = text[i+1:]
		}
	}
	return text, int64(len(raw)), nil
}
