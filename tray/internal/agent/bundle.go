package agent

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"fmt"
	"regexp"
	"strings"
	"time"
)

// maxBundleFileBytes caps one log file inside the bundle.
const maxBundleFileBytes = 4 * 1024 * 1024

var (
	sectionHeaderRe = regexp.MustCompile(`(?m)^### (.+?) Log[ \t]*$`)
	unsafeNameRe    = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
)

// LogFile is one log source's text inside the bundle.
type LogFile struct {
	Name    string // file name inside the archive, e.g. "System.log"
	Source  string // the log source, e.g. "System"
	Content string // the source's section, starting with its "### <source> Log" header
}

// SplitLogSections splits collected text into one file per "### <source> Log"
// section. Text with no headers becomes a single "logs.txt".
func SplitLogSections(text string) []LogFile {
	matches := sectionHeaderRe.FindAllStringSubmatchIndex(text, -1)
	if len(matches) == 0 {
		if strings.TrimSpace(text) == "" {
			return nil
		}
		return []LogFile{{Name: "logs.txt", Source: "logs", Content: text}}
	}
	files := make([]LogFile, 0, len(matches))
	used := map[string]int{}
	for i, m := range matches {
		end := len(text)
		if i+1 < len(matches) {
			end = matches[i+1][0]
		}
		source := text[m[2]:m[3]]
		base := strings.Trim(unsafeNameRe.ReplaceAllString(source, "_"), "._")
		if base == "" {
			base = "log"
		}
		used[base]++
		if used[base] > 1 {
			base = fmt.Sprintf("%s-%d", base, used[base])
		}
		files = append(files, LogFile{
			Name:    base + ".log",
			Source:  source,
			Content: strings.TrimRight(text[m[0]:end], "\n") + "\n",
		})
	}
	return files
}

// bundleLogs packs each log source as its own file in a gzip-compressed tar
// archive (.tar.gz). It returns nil on failure.
func bundleLogs(text string) []byte {
	files := SplitLogSections(text)
	if len(files) == 0 {
		return nil
	}
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(zw)
	now := time.Now()
	for _, f := range files {
		content := f.Content
		if len(content) > maxBundleFileBytes {
			content, _ = Truncate(content, maxBundleFileBytes)
		}
		hdr := &tar.Header{
			Name:    f.Name,
			Mode:    0o644,
			Size:    int64(len(content)),
			ModTime: now,
			Format:  tar.FormatPAX,
		}
		if err := tw.WriteHeader(hdr); err != nil {
			return nil
		}
		if _, err := tw.Write([]byte(content)); err != nil {
			return nil
		}
	}
	if err := tw.Close(); err != nil {
		return nil
	}
	if err := zw.Close(); err != nil {
		return nil
	}
	return buf.Bytes()
}
