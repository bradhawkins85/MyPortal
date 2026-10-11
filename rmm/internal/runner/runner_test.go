package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os/exec"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func checksum(script string) string {
	sum := sha256.Sum256([]byte(script))
	return hex.EncodeToString(sum[:])
}

func TestShellArgsFollowDeclaredOrderAndTypes(t *testing.T) {
	job := client.Job{
		Parameters: map[string]interface{}{
			"Service": "spooler", "Retries": float64(3), "Force": true, "Quiet": false,
			"Paths": []interface{}{"/a", "/b"},
		},
		ParameterOrder: []string{"Service", "Retries", "Force", "Quiet", "Paths"},
	}
	got := ShellArgs(job)
	want := []string{"--Service", "spooler", "--Retries", "3", "--Force", "--Paths", "/a,/b"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ShellArgs = %v, want %v", got, want)
	}
}

func TestParseMarkers(t *testing.T) {
	out := "hello\n##myportal[asset.BitLocker Status]=On\n  ##myportal[company.Tenant]=contoso  \n##myportal[session.id]=123 456\n##myportal[other.x]=1\n"
	got := ParseMarkers(out)
	want := []client.CustomValue{
		{Scope: "asset", Name: "BitLocker Status", Value: "On"},
		{Scope: "company", Name: "Tenant", Value: "contoso"},
		{Scope: "session", Name: "id", Value: "123 456"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseMarkers = %#v", got)
	}
}

func TestParseResultFileAcceptsBothShapes(t *testing.T) {
	list, err := ParseResultFile([]byte(`[{"scope":"Asset","name":"Disk","value":42}]`))
	if err != nil || len(list) != 1 || list[0] != (client.CustomValue{Scope: "asset", Name: "Disk", Value: "42"}) {
		t.Fatalf("array form: %#v %v", list, err)
	}
	grouped, err := ParseResultFile([]byte("\xEF\xBB\xBF" + `{"company":{"B":true},"asset":{"A":"x"}}`))
	if err != nil || len(grouped) != 2 || grouped[0].Scope != "asset" || grouped[1].Value != "true" {
		t.Fatalf("object form: %#v %v", grouped, err)
	}
	// UTF-16 LE with BOM, as written by Windows PowerShell's Out-File.
	text := `[{"scope":"asset","name":"N","value":"v"}]`
	utf16 := []byte{0xFF, 0xFE}
	for _, r := range text {
		utf16 = append(utf16, byte(r), 0)
	}
	values, err := ParseResultFile(utf16)
	if err != nil || len(values) != 1 || values[0].Name != "N" {
		t.Fatalf("utf-16 form: %#v %v", values, err)
	}
	if _, err := ParseResultFile([]byte(`"nope"`)); err == nil {
		t.Fatal("expected an error for a bare string")
	}
}

func TestPowerShellWrapperQuotesPaths(t *testing.T) {
	wrapper := PowerShellWrapper(`C:\it's\script.ps1`, `C:\p.json`)
	if !strings.Contains(wrapper, `& 'C:\it''s\script.ps1' @h`) {
		t.Fatalf("script path not quoted: %s", wrapper)
	}
	if !strings.Contains(wrapper, "exit $LASTEXITCODE") {
		t.Fatal("wrapper must pass the script's exit code through")
	}
}

func TestRunRejectsChecksumMismatch(t *testing.T) {
	r := New(t.TempDir())
	result := r.Run(context.Background(), client.Job{ID: 1, Language: "bash", Script: "echo hi", SHA256: "00"})
	if result.Error == "" || result.ExitCode != nil {
		t.Fatalf("expected checksum failure, got %#v", result)
	}
}

func requireBash(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("bash scripts do not run on Windows")
	}
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not installed")
	}
}

func TestRunBashScriptWithArgsEnvAndCustomValues(t *testing.T) {
	requireBash(t)
	script := strings.Join([]string{
		"#!/bin/bash",
		`echo "args: $*"`,
		`echo "site: $SITE_CODE"`,
		`echo "##myportal[asset.Status]=ok"`,
		`echo '{"company":{"Tenant":"contoso"}}' > "$MYPORTAL_RESULT_FILE"`,
		`echo oops >&2`,
		"exit 3",
	}, "\n")
	job := client.Job{
		ID: 7, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 30,
		Parameters: map[string]interface{}{"Name": "two words"}, ParameterOrder: []string{"Name"},
		Env: map[string]string{"SITE_CODE": "SYD"},
	}
	result := New(t.TempDir()).Run(context.Background(), job)
	if result.ExitCode == nil || *result.ExitCode != 3 {
		t.Fatalf("exit code = %v (%s)", result.ExitCode, result.Error)
	}
	if !strings.Contains(result.Stdout, "args: --Name two words") || !strings.Contains(result.Stdout, "site: SYD") {
		t.Fatalf("stdout = %q", result.Stdout)
	}
	if strings.TrimSpace(result.Stderr) != "oops" {
		t.Fatalf("stderr = %q", result.Stderr)
	}
	want := []client.CustomValue{{Scope: "asset", Name: "Status", Value: "ok"}, {Scope: "company", Name: "Tenant", Value: "contoso"}}
	if !reflect.DeepEqual(result.CustomValues, want) {
		t.Fatalf("custom values = %#v", result.CustomValues)
	}
}

func TestRunStopsAtTimeout(t *testing.T) {
	requireBash(t)
	script := "sleep 30 & wait"
	job := client.Job{ID: 8, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 1}
	result := New(t.TempDir()).Run(context.Background(), job)
	if !result.TimedOut {
		t.Fatalf("expected a timeout, got %#v", result)
	}
}

func TestOutputIsCapped(t *testing.T) {
	buf := &limitedBuffer{limit: 4}
	_, _ = buf.Write([]byte("abcdef"))
	if !strings.HasPrefix(buf.String(), "abcd") || !strings.Contains(buf.String(), "truncated") {
		t.Fatalf("buffer = %q", buf.String())
	}
}
