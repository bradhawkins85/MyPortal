package agent

import (
	"bytes"
	"encoding/xml"
	"fmt"
	"io"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"
)

// maxWinEvents caps the number of entries read per channel so a busy machine
// cannot blow past the prompt budget.
const maxWinEvents = 500

// maxEventMessage caps one event's message in the collected text.
const maxEventMessage = 400

// WevtutilQueryArgs builds the read-only `wevtutil qe` arguments for one
// channel: newest events first, within the spec's window, rendered as XML so
// each event can be reduced to a single line.
func WevtutilQueryArgs(spec LogSpec) []string {
	ms := spec.Window.Milliseconds()
	if ms <= 0 {
		ms = DefaultWindow.Milliseconds()
	}
	return []string{
		"qe", spec.Source,
		fmt.Sprintf("/q:*[System[TimeCreated[timediff(@SystemTime) <= %d]]]", ms),
		fmt.Sprintf("/c:%d", maxWinEvents),
		"/rd:true",
		"/f:RenderedXml",
	}
}

type renderedEvent struct {
	System struct {
		Provider struct {
			Name string `xml:"Name,attr"`
		} `xml:"Provider"`
		EventID     string `xml:"EventID"`
		Level       int    `xml:"Level"`
		TimeCreated struct {
			SystemTime string `xml:"SystemTime,attr"`
		} `xml:"TimeCreated"`
	} `xml:"System"`
	RenderingInfo struct {
		Message string `xml:"Message"`
		Level   string `xml:"Level"`
	} `xml:"RenderingInfo"`
}

var levelNames = map[int]string{
	0: "Information",
	1: "Critical",
	2: "Error",
	3: "Warning",
	4: "Information",
	5: "Verbose",
}

// FormatRenderedEvents turns `wevtutil qe /f:RenderedXml` output (a sequence
// of <Event> elements with no root) into one line per event:
//
//	2026-10-06 10:00:00 [Error] id=7000 Service Control Manager: message
//
// It returns whatever it could format along with any parse error.
func FormatRenderedEvents(raw []byte) (string, error) {
	data := normaliseText(raw)
	dec := xml.NewDecoder(strings.NewReader(data))
	dec.Strict = false
	var lines []string
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return strings.Join(lines, "\n"), err
		}
		start, ok := tok.(xml.StartElement)
		if !ok || start.Name.Local != "Event" {
			continue
		}
		var ev renderedEvent
		if err := dec.DecodeElement(&ev, &start); err != nil {
			return strings.Join(lines, "\n"), err
		}
		lines = append(lines, formatEvent(ev))
	}
	return strings.Join(lines, "\n"), nil
}

func formatEvent(ev renderedEvent) string {
	when := ev.System.TimeCreated.SystemTime
	if t, err := time.Parse(time.RFC3339Nano, when); err == nil {
		when = t.Local().Format("2006-01-02 15:04:05")
	}
	level := strings.TrimSpace(ev.RenderingInfo.Level)
	if level == "" {
		level = levelNames[ev.System.Level]
	}
	if level == "" {
		level = fmt.Sprintf("Level %d", ev.System.Level)
	}
	msg := strings.Join(strings.Fields(ev.RenderingInfo.Message), " ")
	if len(msg) > maxEventMessage {
		msg = msg[:maxEventMessage]
		for !utf8.ValidString(msg) && len(msg) > 0 {
			msg = msg[:len(msg)-1]
		}
	}
	provider := strings.TrimSpace(ev.System.Provider.Name)
	line := fmt.Sprintf("%s [%s] id=%s", when, level, strings.TrimSpace(ev.System.EventID))
	if provider != "" {
		line += " " + provider
	}
	if msg != "" {
		line += ": " + msg
	}
	return line
}

// normaliseText decodes UTF-16 output (with or without a BOM) and replaces
// invalid UTF-8 so the XML decoder never rejects a code-page byte.
func normaliseText(raw []byte) string {
	if len(raw) >= 2 && ((raw[0] == 0xFF && raw[1] == 0xFE) || looksUTF16LE(raw)) {
		if raw[0] == 0xFF && raw[1] == 0xFE {
			raw = raw[2:]
		}
		u := make([]uint16, len(raw)/2)
		for i := range u {
			u[i] = uint16(raw[2*i]) | uint16(raw[2*i+1])<<8
		}
		return string(utf16.Decode(u))
	}
	raw = bytes.TrimPrefix(raw, []byte("\xEF\xBB\xBF"))
	return strings.ToValidUTF8(string(raw), "?")
}

// looksUTF16LE reports whether ASCII-range text has a zero high byte, as
// UTF-16LE output from a Windows tool does.
func looksUTF16LE(raw []byte) bool {
	n := len(raw)
	if n > 64 {
		n = 64
	}
	if n < 4 {
		return false
	}
	zeros := 0
	for i := 1; i < n; i += 2 {
		if raw[i] == 0 {
			zeros++
		}
	}
	return zeros*2 >= n/2
}
