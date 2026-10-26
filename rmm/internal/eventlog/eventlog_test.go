package eventlog

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

const rendered = `<Events><Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Service Control Manager' Guid='{555908d1}'/><EventID Qualifiers='16384'>7036</EventID><Level>4</Level><TimeCreated SystemTime='2026-10-11T10:00:00.1234567Z'/><EventRecordID>41</EventRecordID><Channel>System</Channel><Computer>PC-01.contoso.local</Computer></System><EventData><Data Name='param1'>Print Spooler</Data></EventData><RenderingInfo Culture='en-US'><Message>The Print Spooler service entered the running state.` + "\x01" + `</Message><Level>Information</Level></RenderingInfo></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Broken'/><EventID>1</EventID><TimeCreated SystemTime='not a time'/><EventRecordID>42</EventRecordID></System></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='App'/><EventID>1000</EventID><Level>2</Level><TimeCreated SystemTime='2026-10-11T10:05:00Z'/><EventRecordID>43</EventRecordID><Computer>PC-01</Computer></System><EventData><Data Name='Application'>app.exe</Data><Data>&#x1;crashed</Data></EventData></Event></Events>`

func TestParseKeepsGoodEventsAndCountsDamagedOnes(t *testing.T) {
	entries, highest, count := Parse(rendered, "System")
	if highest != 43 || count != 3 || len(entries) != 2 {
		t.Fatalf("highest=%d count=%d entries=%#v", highest, count, entries)
	}
	first := entries[0]
	if first.RecordID != 41 || first.EventID != 7036 || first.Level != 4 || first.Provider != "Service Control Manager" ||
		first.Computer != "PC-01.contoso.local" || first.Message != "The Print Spooler service entered the running state." ||
		!first.TimeCreated.Equal(time.Date(2026, 10, 11, 10, 0, 0, 123456700, time.UTC)) || first.LogName != "System" {
		t.Fatalf("first = %#v", first)
	}
	// Without a rendered message the event data is shown.
	if entries[1].Message != "Application: app.exe\ncrashed" || entries[1].Level != 2 {
		t.Fatalf("second = %#v", entries[1])
	}
}

func TestDecodeOutputReadsUTF16(t *testing.T) {
	text := "<Event>Größe 日本</Event>"
	units := utf16.Encode([]rune(text))
	data := []byte{0xFF, 0xFE}
	for _, unit := range units {
		data = append(data, byte(unit), byte(unit>>8))
	}
	if got := DecodeOutput(data); got != text {
		t.Fatalf("with BOM: %q", got)
	}
	if got := DecodeOutput(data[2:]); got != text {
		t.Fatalf("without BOM: %q", got)
	}
	if got := DecodeOutput([]byte(text)); got != text {
		t.Fatalf("utf-8: %q", got)
	}
}

func TestQuery(t *testing.T) {
	now := time.Date(2026, 10, 11, 12, 0, 0, 0, time.UTC)
	if q := Query(99, time.Time{}, now); q != "*[System[(EventRecordID>99)]]" {
		t.Fatal(q)
	}
	if q := Query(0, now.Add(-24*time.Hour), now); q != "*[System[TimeCreated[timediff(@SystemTime)<=86400000]]]" {
		t.Fatal(q)
	}
}

func TestNewestRecordID(t *testing.T) {
	if id := NewestRecordID("<Event><System><EventRecordID>1234</EventRecordID></System></Event>"); id != 1234 {
		t.Fatal(id)
	}
	if id := NewestRecordID(""); id != 0 {
		t.Fatal(id)
	}
}

type fakeSource struct {
	records []uint64
	reads   []uint64
	levels  map[uint64]int // level 4 when missing
}

func (f *fakeSource) Read(_ context.Context, log string, after uint64, _ time.Time, max int) ([]client.EventLogEntry, uint64, int, error) {
	f.reads = append(f.reads, after)
	var entries []client.EventLogEntry
	var highest uint64
	for _, id := range f.records {
		if id > after && len(entries) < max {
			level, ok := f.levels[id]
			if !ok {
				level = 4
			}
			entries = append(entries, client.EventLogEntry{LogName: log, RecordID: id, Level: level})
			highest = id
		}
	}
	return entries, highest, len(entries), nil
}

func (f *fakeSource) Newest(context.Context, string) (uint64, error) {
	if len(f.records) == 0 {
		return 0, nil
	}
	return f.records[len(f.records)-1], nil
}

func TestCollectorSendsInBatchesAndRemembersItsPlace(t *testing.T) {
	source := &fakeSource{records: []uint64{1, 2, 3, 4, 5}}
	var sent [][]uint64
	path := filepath.Join(t.TempDir(), "cursors.json")
	collector := &Collector{
		Source: source,
		Send: func(_ context.Context, entries []client.EventLogEntry) error {
			var ids []uint64
			for _, entry := range entries {
				ids = append(ids, entry.RecordID)
			}
			sent = append(sent, ids)
			return nil
		},
		Cursors: Cursors{},
		Path:    path,
	}
	if err := collector.SyncLog(context.Background(), "System", 2, time.Hour); err != nil {
		t.Fatal(err)
	}
	if len(sent) != 3 || sent[2][0] != 5 || LoadCursors(path)["system"] != 5 {
		t.Fatalf("sent=%v cursors=%v", sent, LoadCursors(path))
	}

	// The log was cleared: record IDs start again, so the agent starts over.
	source.records = []uint64{1}
	if err := collector.SyncLog(context.Background(), "System", 2, time.Hour); err != nil {
		t.Fatal(err)
	}
	if _, ok := LoadCursors(path)["system"]; ok {
		t.Fatalf("cursor kept after the log was cleared: %v", LoadCursors(path))
	}
	if err := collector.SyncLog(context.Background(), "System", 2, time.Hour); err != nil {
		t.Fatal(err)
	}
	if LoadCursors(path)["system"] != 1 {
		t.Fatalf("cursors = %v", LoadCursors(path))
	}
}

func TestCollectorLeavesOutLessSevereEventsButMovesPastThem(t *testing.T) {
	// 1 information, 2 warning, 3 error, 4 log always, 5 verbose, 6 information.
	source := &fakeSource{records: []uint64{1, 2, 3, 4, 5, 6}, levels: map[uint64]int{2: 3, 3: 2, 4: 0, 5: 5}}
	var sent []uint64
	path := filepath.Join(t.TempDir(), "cursors.json")
	collector := &Collector{
		Source: source,
		Send: func(_ context.Context, entries []client.EventLogEntry) error {
			for _, entry := range entries {
				sent = append(sent, entry.RecordID)
			}
			return nil
		},
		Cursors:  Cursors{},
		Path:     path,
		MaxLevel: 3,
	}
	if err := collector.SyncLog(context.Background(), "System", 2, time.Hour); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(sent) != "[2 3 4]" || LoadCursors(path)["system"] != 6 {
		t.Fatalf("sent=%v cursors=%v", sent, LoadCursors(path))
	}
	for _, maxLevel := range []int{0, 5} {
		if !Keep(5, maxLevel) || !Keep(200, maxLevel) {
			t.Fatalf("max level %d should keep everything", maxLevel)
		}
	}
	if Keep(4, 3) || !Keep(0, 1) || !Keep(1, 1) || Keep(2, 1) {
		t.Fatal("Keep compares levels wrongly")
	}
}

func TestCollectorKeepsItsPlaceWhenSendingFails(t *testing.T) {
	source := &fakeSource{records: []uint64{7, 8}}
	failure := errors.New("offline")
	collector := &Collector{
		Source:  source,
		Send:    func(context.Context, []client.EventLogEntry) error { return failure },
		Cursors: Cursors{"system": 6},
	}
	if err := collector.SyncLog(context.Background(), "System", 10, time.Hour); !errors.Is(err, failure) {
		t.Fatalf("err = %v", err)
	}
	if collector.Cursors["system"] != 6 {
		t.Fatalf("cursor moved: %v", collector.Cursors)
	}
}

func TestTruncateCountsRunes(t *testing.T) {
	if got := truncate(strings.Repeat("é", 5), 3); got != "ééé" {
		t.Fatal(got)
	}
}
