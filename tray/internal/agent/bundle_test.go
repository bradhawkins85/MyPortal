package agent

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestBundleHasOneFilePerSource(t *testing.T) {
	text := "### System Log\n2026-10-06 [Error] id=1 disk\n\n" +
		"### Microsoft-Windows-WLAN-AutoConfig/Operational Log\n2026-10-06 [Warning] id=8002 wifi\n\n" +
		"### Security Log\n[unavailable: access denied]\n\n"
	files, err := untar(bundleLogs(text))
	if err != nil {
		t.Fatalf("bundle is not a valid .tar.gz: %v", err)
	}
	wantNames := []string{"System.log", "Microsoft-Windows-WLAN-AutoConfig_Operational.log", "Security.log"}
	if len(files) != len(wantNames) {
		t.Fatalf("files = %v, want %v", files, wantNames)
	}
	for i, name := range wantNames {
		if files[i].Name != name {
			t.Errorf("file %d = %q, want %q", i, files[i].Name, name)
		}
	}
	if !strings.HasPrefix(files[1].Content, "### Microsoft-Windows-WLAN-AutoConfig/Operational Log\n") ||
		!strings.Contains(files[1].Content, "id=8002 wifi") || strings.Contains(files[1].Content, "disk") {
		t.Errorf("WLAN file has the wrong content: %q", files[1].Content)
	}
}

func TestBundleWithoutHeadersIsOneFile(t *testing.T) {
	files, err := untar(bundleLogs("plain text"))
	if err != nil || len(files) != 1 || files[0].Name != "logs.txt" {
		t.Fatalf("files = %v, err = %v", files, err)
	}
}

func TestRunFlagsWhenNoSourceCouldBeRead(t *testing.T) {
	a := testAgent(
		func(context.Context, []LogSpec) (string, []string, error) {
			return "### System Log\n[unavailable: exit status 1]\n", nil, nil
		},
		nil,
	)
	res, err := a.Run(context.Background(), Request{Endpoint: "h"})
	if err == nil || !strings.Contains(err.Error(), "no log source could be read") {
		t.Fatalf("expected an error saying no source was read, got %v", err)
	}
	if len(res.LogBundle) == 0 {
		t.Error("the bundle should still carry each source's error")
	}
}

func TestWevtutilQueryArgs(t *testing.T) {
	args := WevtutilQueryArgs(LogSpec{Source: "Microsoft-Windows-Windows Defender/Operational", Window: 6 * time.Hour})
	want := []string{
		"qe", "Microsoft-Windows-Windows Defender/Operational",
		"/q:*[System[TimeCreated[timediff(@SystemTime) <= 21600000]]]",
		"/c:500", "/rd:true", "/f:RenderedXml",
	}
	if strings.Join(args, "|") != strings.Join(want, "|") {
		t.Errorf("args = %q, want %q", args, want)
	}
}

const renderedSample = `<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Service Control Manager' Guid='{555908d1}'/><EventID Qualifiers='49152'>7000</EventID><Level>2</Level><TimeCreated SystemTime='2026-10-06T10:00:00.1234567Z'/><Channel>System</Channel></System><EventData/><RenderingInfo Culture='en-US'><Message>The Spooler service failed to start
due to the following error: Access is denied.</Message><Level>Error</Level></RenderingInfo></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Kernel-Power'/><EventID>41</EventID><Level>1</Level><TimeCreated SystemTime='2026-10-06T09:00:00Z'/></System></Event>`

func TestFormatRenderedEvents(t *testing.T) {
	text, err := FormatRenderedEvents([]byte(renderedSample))
	if err != nil {
		t.Fatalf("FormatRenderedEvents: %v", err)
	}
	lines := strings.Split(text, "\n")
	if len(lines) != 2 {
		t.Fatalf("lines = %q", lines)
	}
	if !strings.Contains(lines[0], "[Error] id=7000 Service Control Manager: The Spooler service failed to start due to the following error: Access is denied.") {
		t.Errorf("line 0 = %q", lines[0])
	}
	if !strings.Contains(lines[1], "[Critical] id=41 Kernel-Power") {
		t.Errorf("line 1 = %q", lines[1])
	}
}

func TestFormatRenderedEventsUTF16(t *testing.T) {
	src := []rune(renderedSample)
	raw := []byte{0xFF, 0xFE}
	for _, r := range src {
		raw = append(raw, byte(r), byte(r>>8))
	}
	text, err := FormatRenderedEvents(raw)
	if err != nil || !strings.Contains(text, "id=7000") {
		t.Fatalf("text = %q, err = %v", text, err)
	}
}

func TestFormatRenderedEventsEmpty(t *testing.T) {
	text, err := FormatRenderedEvents(nil)
	if err != nil || text != "" {
		t.Fatalf("text = %q, err = %v", text, err)
	}
}
