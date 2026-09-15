package defender

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// ApplyExclusions adds the effective portal policy to Microsoft Defender.
// Add-MpPreference is intentionally used rather than Set-MpPreference so
// exclusions configured locally or by another management system are retained.
func ApplyExclusions(exclusions []api.DefenderExclusion) error {
	supported := make([]api.DefenderExclusion, 0, len(exclusions))
	var unsupported []string
	for _, exclusion := range exclusions {
		switch strings.ToLower(strings.TrimSpace(exclusion.Type)) {
		case "path", "process", "extension":
			supported = append(supported, exclusion)
		default:
			unsupported = append(unsupported, exclusion.Type)
		}
	}
	script, err := exclusionScript(supported)
	if err != nil {
		return err
	}
	if script != "" {
		if err := executePowerShell(script); err != nil {
			return err
		}
	}
	if len(unsupported) > 0 {
		return fmt.Errorf("unsupported Microsoft Defender exclusion types were skipped: %s", strings.Join(unsupported, ", "))
	}
	return nil
}

func exclusionScript(exclusions []api.DefenderExclusion) (string, error) {
	grouped := map[string][]string{
		"path": {}, "process": {}, "extension": {},
	}
	for _, exclusion := range exclusions {
		kind := strings.ToLower(strings.TrimSpace(exclusion.Type))
		value := strings.TrimSpace(exclusion.Value)
		if value == "" {
			return "", fmt.Errorf("Microsoft Defender %s exclusion has an empty value", kind)
		}
		if _, supported := grouped[kind]; !supported {
			return "", fmt.Errorf("unsupported Microsoft Defender exclusion type %q", exclusion.Type)
		}
		grouped[kind] = append(grouped[kind], value)
	}
	if len(grouped["path"])+len(grouped["process"])+len(grouped["extension"]) == 0 {
		return "", nil
	}

	payload, err := json.Marshal(grouped)
	if err != nil {
		return "", fmt.Errorf("encode Microsoft Defender exclusions: %w", err)
	}
	encoded := base64.StdEncoding.EncodeToString(payload)
	// Values are transported as base64 JSON rather than interpolated into the
	// script, preventing an exclusion value from becoming PowerShell code.
	return fmt.Sprintf(`$policyJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s'))
$policy = $policyJson | ConvertFrom-Json
$preference = Get-MpPreference
$definitions = @(
  @{ Name = 'Path'; Values = @($policy.path); Current = @($preference.ExclusionPath) },
  @{ Name = 'Process'; Values = @($policy.process); Current = @($preference.ExclusionProcess) },
  @{ Name = 'Extension'; Values = @($policy.extension); Current = @($preference.ExclusionExtension) }
)
foreach ($definition in $definitions) {
  $requested = @($definition.Values | ForEach-Object {
    if (-not [string]::IsNullOrWhiteSpace([string]$_)) {
      [Environment]::ExpandEnvironmentVariables(([string]$_).Trim())
    }
  } | Sort-Object -Unique)
  $missing = @($requested | Where-Object { $definition.Current -notcontains $_ })
  if ($missing.Count -gt 0) {
    $parameters = @{ ('Exclusion' + $definition.Name) = $missing }
    Add-MpPreference @parameters
  }
}`, encoded), nil
}
