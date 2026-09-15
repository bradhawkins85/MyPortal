package defender

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestExclusionScriptCarriesValuesAsEncodedData(t *testing.T) {
	exclusions := []api.DefenderExclusion{
		{Type: "path", Value: `C:\Program Files\Contoso`},
		{Type: "process", Value: `worker.exe`},
		{Type: "extension", Value: `.cache`},
	}
	script, err := exclusionScript(exclusions)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(script, exclusions[0].Value) {
		t.Fatal("an exclusion value was interpolated into PowerShell")
	}
	if !strings.Contains(script, "Add-MpPreference") || !strings.Contains(script, "Get-MpPreference") {
		t.Fatalf("script does not reconcile Defender preferences: %s", script)
	}

	payload, _ := json.Marshal(map[string][]string{
		"path": {exclusions[0].Value}, "process": {exclusions[1].Value}, "extension": {exclusions[2].Value},
	})
	if !strings.Contains(script, base64.StdEncoding.EncodeToString(payload)) {
		t.Fatal("script does not contain the encoded policy")
	}
}

func TestExclusionScriptRejectsUnsupportedTypes(t *testing.T) {
	_, err := exclusionScript([]api.DefenderExclusion{{Type: "registry", Value: `HKLM\Software\Contoso`}})
	if err == nil {
		t.Fatal("expected registry exclusion to be rejected because Defender does not support it")
	}
}

func TestExclusionScriptDoesNothingForEmptyPolicy(t *testing.T) {
	script, err := exclusionScript(nil)
	if err != nil || script != "" {
		t.Fatalf("exclusionScript(nil) = %q, %v", script, err)
	}
}
