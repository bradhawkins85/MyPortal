// Package eventlog sends Windows event logs to MyPortal.
//
// MyPortal says which logs to collect (Application and System, plus any
// extra logs chosen for the company or device). For each log the agent keeps
// the last record ID it sent in eventlog-cursors.json in its state folder, so
// it carries on where it left off after a restart. A log never sent before
// starts from the last InitialLookbackHours. When a log is cleared its record
// IDs start again from 1, which the agent notices and starts over.
//
// Events less severe than MyPortal keeps (Collector.MaxLevel) are read but
// not sent, and the cursor still moves past them.
package eventlog

import (
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

const (
	maxMessageRunes = 30000
	maxNameRunes    = 255
	// batchesPerCycle caps how much one log sends per cycle, so a busy log
	// does not hold up the others.
	batchesPerCycle = 20
)

// ErrNoLog means the device has no event log with that name, such as an
// event source entered as a log or a log for a feature not installed.
var ErrNoLog = errors.New("this device has no event log with that name")

// Source reads one Windows event log.
type Source interface {
	// Read returns up to max events oldest first: those after record ID
	// after, or when after is 0, those created since since. highest is the
	// largest record ID read, including events that could not be parsed,
	// and count how many events were read.
	Read(ctx context.Context, log string, after uint64, since time.Time, max int) (entries []client.EventLogEntry, highest uint64, count int, err error)
	// Newest returns the newest record ID in the log, or 0 when it is empty.
	Newest(ctx context.Context, log string) (uint64, error)
}

// Cursors maps a log name (lower case) to the last record ID sent.
type Cursors map[string]uint64

// LoadCursors reads the saved cursors; a missing or damaged file starts afresh.
func LoadCursors(path string) Cursors {
	cursors := Cursors{}
	data, err := os.ReadFile(path)
	if err == nil {
		_ = json.Unmarshal(data, &cursors)
	}
	return cursors
}

// Save writes the cursors with owner-only permissions.
func (c Cursors) Save(path string) error {
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Collector sends new entries from each log.
type Collector struct {
	Source  Source
	Send    func(ctx context.Context, entries []client.EventLogEntry) error
	Cursors Cursors
	Path    string
	Now     func() time.Time
	// MaxLevel is the least severe level sent: 1 critical, 2 error,
	// 3 warning, 4 information, 5 or 0 everything. "Log always" events
	// (level 0) are always sent.
	MaxLevel int
}

// Keep reports whether an event at level is sent when maxLevel is the least
// severe level kept.
func Keep(level, maxLevel int) bool {
	return maxLevel <= 0 || maxLevel >= 5 || level == 0 || level <= maxLevel
}

func (c *Collector) keep(entries []client.EventLogEntry) []client.EventLogEntry {
	kept := entries[:0]
	for _, entry := range entries {
		if Keep(entry.Level, c.MaxLevel) {
			kept = append(kept, entry)
		}
	}
	return kept
}

// SyncLog sends everything new in one log, a batch at a time.
func (c *Collector) SyncLog(ctx context.Context, log string, maxBatch int, lookback time.Duration) error {
	if maxBatch <= 0 {
		maxBatch = 500
	}
	now := time.Now
	if c.Now != nil {
		now = c.Now
	}
	key := strings.ToLower(log)
	for i := 0; i < batchesPerCycle && ctx.Err() == nil; i++ {
		after := c.Cursors[key]
		entries, highest, count, err := c.Source.Read(ctx, log, after, now().Add(-lookback), maxBatch)
		if err != nil {
			return err
		}
		if count == 0 {
			if after > 0 {
				// A cleared log numbers its records from 1 again.
				if newest, err := c.Source.Newest(ctx, log); err == nil && newest < after {
					delete(c.Cursors, key)
					return c.save()
				}
			}
			return nil
		}
		if entries = c.keep(entries); len(entries) > 0 {
			if err := c.Send(ctx, entries); err != nil {
				return err
			}
		}
		if highest > after {
			c.Cursors[key] = highest
			if err := c.save(); err != nil {
				return err
			}
		}
		if count < maxBatch {
			return nil
		}
	}
	return nil
}

func (c *Collector) save() error {
	if c.Path == "" {
		return nil
	}
	return c.Cursors.Save(c.Path)
}

// Query is the XPath for events after a record ID, or since a time.
func Query(after uint64, since time.Time, now time.Time) string {
	if after > 0 {
		return "*[System[(EventRecordID>" + strconv.FormatUint(after, 10) + ")]]"
	}
	ms := now.Sub(since).Milliseconds()
	if ms < 0 {
		ms = 0
	}
	return "*[System[TimeCreated[timediff(@SystemTime)<=" + strconv.FormatInt(ms, 10) + "]]]"
}

type xmlEvent struct {
	System struct {
		Provider struct {
			Name string `xml:"Name,attr"`
		} `xml:"Provider"`
		EventID     string `xml:"EventID"`
		Level       string `xml:"Level"`
		TimeCreated struct {
			SystemTime string `xml:"SystemTime,attr"`
		} `xml:"TimeCreated"`
		EventRecordID string `xml:"EventRecordID"`
		Channel       string `xml:"Channel"`
		Computer      string `xml:"Computer"`
	} `xml:"System"`
	EventData struct {
		Data []struct {
			Name  string `xml:"Name,attr"`
			Value string `xml:",chardata"`
		} `xml:"Data"`
	} `xml:"EventData"`
	RenderingInfo struct {
		Message string `xml:"Message"`
	} `xml:"RenderingInfo"`
}

var recordIDPattern = regexp.MustCompile(`<EventRecordID>\s*(\d+)\s*</EventRecordID>`)

// Parse reads wevtutil's /f:RenderedXml output. Each <Event> is parsed on
// its own so one damaged event does not lose the batch; its record ID still
// counts towards highest, so it is not read again.
func Parse(output, log string) (entries []client.EventLogEntry, highest uint64, count int) {
	for _, chunk := range splitEvents(output) {
		count++
		if match := recordIDPattern.FindStringSubmatch(chunk); match != nil {
			if id, err := strconv.ParseUint(match[1], 10, 64); err == nil && id > highest {
				highest = id
			}
		}
		var event xmlEvent
		if err := xml.Unmarshal([]byte(cleanXML(chunk)), &event); err != nil {
			continue
		}
		recordID, err := strconv.ParseUint(strings.TrimSpace(event.System.EventRecordID), 10, 64)
		if err != nil {
			continue
		}
		created, err := time.Parse(time.RFC3339Nano, strings.TrimSpace(event.System.TimeCreated.SystemTime))
		if err != nil {
			continue
		}
		eventID, _ := strconv.ParseUint(strings.TrimSpace(event.System.EventID), 10, 32)
		level, err := strconv.Atoi(strings.TrimSpace(event.System.Level))
		if err != nil || level < 0 || level > 255 {
			level = 4
		}
		message := strings.TrimSpace(event.RenderingInfo.Message)
		if message == "" {
			// No message template on this machine: show the raw event data.
			var parts []string
			for _, data := range event.EventData.Data {
				value := strings.TrimSpace(data.Value)
				if value == "" {
					continue
				}
				if data.Name != "" {
					value = data.Name + ": " + value
				}
				parts = append(parts, value)
			}
			message = strings.Join(parts, "\n")
		}
		channel := strings.TrimSpace(event.System.Channel)
		if channel == "" {
			channel = log
		}
		entries = append(entries, client.EventLogEntry{
			LogName:     log,
			RecordID:    recordID,
			EventID:     uint32(eventID),
			Level:       level,
			Provider:    truncate(strings.TrimSpace(event.System.Provider.Name), maxNameRunes),
			Computer:    truncate(strings.TrimSpace(event.System.Computer), maxNameRunes),
			Message:     truncate(message, maxMessageRunes),
			TimeCreated: created.UTC(),
		})
	}
	return entries, highest, count
}

// NewestRecordID reads the record ID from wevtutil's output for one event.
func NewestRecordID(output string) uint64 {
	if match := recordIDPattern.FindStringSubmatch(output); match != nil {
		id, _ := strconv.ParseUint(match[1], 10, 64)
		return id
	}
	return 0
}

func splitEvents(output string) []string {
	var chunks []string
	for {
		start := indexEventStart(output)
		if start < 0 {
			return chunks
		}
		output = output[start:]
		end := strings.Index(output, "</Event>")
		if end < 0 {
			return chunks
		}
		end += len("</Event>")
		chunks = append(chunks, output[:end])
		output = output[end:]
	}
}

// indexEventStart finds "<Event " or "<Event>", skipping "<Events>".
func indexEventStart(s string) int {
	offset := 0
	for {
		i := strings.Index(s[offset:], "<Event")
		if i < 0 {
			return -1
		}
		i += offset
		next := i + len("<Event")
		if next < len(s) && (s[next] == ' ' || s[next] == '>' || s[next] == '\t' || s[next] == '\r' || s[next] == '\n') {
			return i
		}
		offset = next
	}
}

var charRefPattern = regexp.MustCompile(`&#(x[0-9a-fA-F]+|[0-9]+);`)

// cleanXML drops characters XML 1.0 does not allow, which some event
// messages contain and Go's XML parser rejects.
func cleanXML(s string) string {
	s = charRefPattern.ReplaceAllStringFunc(s, func(ref string) string {
		body := ref[2 : len(ref)-1]
		var value uint64
		var err error
		if body[0] == 'x' {
			value, err = strconv.ParseUint(body[1:], 16, 32)
		} else {
			value, err = strconv.ParseUint(body, 10, 32)
		}
		if err != nil || !validXMLRune(rune(value)) {
			return ""
		}
		return ref
	})
	return strings.Map(func(r rune) rune {
		if r == utf8.RuneError || !validXMLRune(r) {
			return -1
		}
		return r
	}, s)
}

func validXMLRune(r rune) bool {
	return r == '\t' || r == '\n' || r == '\r' ||
		(r >= 0x20 && r <= 0xD7FF) || (r >= 0xE000 && r <= 0xFFFD) || (r >= 0x10000 && r <= 0x10FFFF)
}

func truncate(s string, max int) string {
	if utf8.RuneCountInString(s) <= max {
		return s
	}
	runes := []rune(s)
	return string(runes[:max])
}

// DecodeOutput turns wevtutil's /uni:true output (UTF-16LE) into a string;
// anything else is taken as UTF-8.
func DecodeOutput(data []byte) string {
	utf16le := len(data) >= 2 && ((data[0] == 0xFF && data[1] == 0xFE) || (data[1] == 0 && data[0] != 0))
	if !utf16le {
		return string(data)
	}
	if data[0] == 0xFF && data[1] == 0xFE {
		data = data[2:]
	}
	units := make([]uint16, len(data)/2)
	for i := range units {
		units[i] = uint16(data[2*i]) | uint16(data[2*i+1])<<8
	}
	return string(utf16.Decode(units))
}
