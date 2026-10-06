package agent

import (
	"strings"
	"testing"
)

func TestPowerShellErrorTextDecodesCLIXML(t *testing.T) {
	stderr := `#< CLIXML
<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><Obj S="progress" RefId="0"><TN RefId="0"><T>System.Management.Automation.PSCustomObject</T></TN><MS><PR N="Record"><AV>Preparing modules for first use.</AV></PR></MS></Obj><S S="Error">Get-WinEvent : The specified channel could not be found._x000D__x000A_</S><S S="Error">At line:3 char:3_x000D__x000A_</S></Objs>`
	got := PowerShellErrorText(stderr)
	want := "Get-WinEvent : The specified channel could not be found. At line:3 char:3"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
	if strings.Contains(got, "Preparing modules") {
		t.Error("progress records must be dropped")
	}
}

func TestPowerShellErrorTextPlain(t *testing.T) {
	if got := PowerShellErrorText("  access   denied \n"); got != "access denied" {
		t.Errorf("got %q", got)
	}
}

func TestLogQueryScriptUsesFilterHashtable(t *testing.T) {
	if strings.Contains(logQueryScript, "-Since") {
		t.Error("Get-WinEvent has no -Since parameter")
	}
	for _, want := range []string{"-FilterHashtable", "StartTime", "NoMatchingEventsFound", "$ProgressPreference = 'SilentlyContinue'"} {
		if !strings.Contains(logQueryScript, want) {
			t.Errorf("log query script is missing %q", want)
		}
	}
}
