//go:build windows

package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf16"

	"golang.org/x/sys/windows/registry"
)

// Office 2016, 2019, 2021 and Microsoft 365 use 16.0; Office 2013 uses 15.0.
var outlookOfficeVersions = []string{"16.0", "15.0"}

// outlookMailAccountsGUID is the profile subkey that holds mail accounts.
const outlookMailAccountsGUID = "9375CFF0413111d3B88A00104B2A6676"

type windowsOutlookPlatform struct{}

func newOutlookPlatform() outlookPlatform { return windowsOutlookPlatform{} }

func (windowsOutlookPlatform) Supported() bool { return true }

// Accounts walks every Outlook profile for the current user and returns the
// email address of each mail account.
func (windowsOutlookPlatform) Accounts() ([]outlookAccount, error) {
	var accounts []outlookAccount
	for _, version := range outlookOfficeVersions {
		profilesPath := `Software\Microsoft\Office\` + version + `\Outlook\Profiles`
		profiles, err := registry.OpenKey(registry.CURRENT_USER, profilesPath, registry.ENUMERATE_SUB_KEYS)
		if err != nil {
			continue
		}
		profileNames, _ := profiles.ReadSubKeyNames(-1)
		profiles.Close()
		for _, profile := range profileNames {
			mailPath := profilesPath + `\` + profile + `\` + outlookMailAccountsGUID
			mail, err := registry.OpenKey(registry.CURRENT_USER, mailPath, registry.ENUMERATE_SUB_KEYS)
			if err != nil {
				continue
			}
			accountKeys, _ := mail.ReadSubKeyNames(-1)
			mail.Close()
			for _, accountKey := range accountKeys {
				path := mailPath + `\` + accountKey
				address := readAccountAddress(path)
				if address != "" {
					accounts = append(accounts, outlookAccount{Key: path, Address: address})
				}
			}
		}
		if len(accounts) > 0 {
			// Only the newest installed Office version is in use.
			break
		}
	}
	return accounts, nil
}

func readAccountAddress(path string) string {
	key, err := registry.OpenKey(registry.CURRENT_USER, path, registry.QUERY_VALUE)
	if err != nil {
		return ""
	}
	defer key.Close()
	for _, name := range []string{"Account Name", "Email"} {
		value := strings.TrimSpace(readRegistryText(key, name))
		if strings.Contains(value, "@") && !strings.ContainsAny(value, " \t") {
			return value
		}
	}
	return ""
}

// readRegistryText reads a value Outlook may store either as a string or as
// null-terminated UTF-16LE binary data.
func readRegistryText(key registry.Key, name string) string {
	if value, _, err := key.GetStringValue(name); err == nil {
		return value
	}
	data, _, err := key.GetBinaryValue(name)
	if err != nil {
		return ""
	}
	return decodeUTF16Binary(data)
}

func decodeUTF16Binary(data []byte) string {
	units := make([]uint16, 0, len(data)/2)
	for i := 0; i+1 < len(data); i += 2 {
		unit := binary.LittleEndian.Uint16(data[i:])
		if unit == 0 {
			break
		}
		units = append(units, unit)
	}
	return string(utf16.Decode(units))
}

func encodeUTF16Binary(text string) []byte {
	units := append(utf16.Encode([]rune(text)), 0)
	out := make([]byte, 2*len(units))
	for i, unit := range units {
		binary.LittleEndian.PutUint16(out[2*i:], unit)
	}
	return out
}

// SignaturesDir returns %APPDATA%\Microsoft\<Signatures>, honouring the
// localised folder name Outlook records for non-English installations.
func (windowsOutlookPlatform) SignaturesDir() (string, error) {
	appData := os.Getenv("APPDATA")
	if appData == "" {
		return "", errors.New("APPDATA is not set")
	}
	folder := "Signatures"
	for _, version := range outlookOfficeVersions {
		key, err := registry.OpenKey(registry.CURRENT_USER,
			`Software\Microsoft\Office\`+version+`\Common\General`, registry.QUERY_VALUE)
		if err != nil {
			continue
		}
		name, _, err := key.GetStringValue("Signatures")
		key.Close()
		if err == nil && strings.TrimSpace(name) != "" && !strings.ContainsAny(name, `\/:`) {
			folder = strings.TrimSpace(name)
			break
		}
	}
	return filepath.Join(appData, "Microsoft", folder), nil
}

func (windowsOutlookPlatform) SetAccountSignature(account outlookAccount, name string) error {
	key, err := registry.OpenKey(registry.CURRENT_USER, account.Key, registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer key.Close()
	for _, valueName := range []string{"New Signature", "Reply-Forward Signature"} {
		if err := setRegistryTextLike(key, valueName, name); err != nil {
			return fmt.Errorf("%s: %w", valueName, err)
		}
	}
	return nil
}

// setRegistryTextLike writes value using the type Outlook already uses for
// it on this machine, defaulting to a string for new values.
func setRegistryTextLike(key registry.Key, name, value string) error {
	if _, valueType, err := key.GetValue(name, nil); err == nil && valueType == registry.BINARY {
		if readRegistryText(key, name) == value {
			return nil
		}
		return key.SetBinaryValue(name, encodeUTF16Binary(value))
	}
	if current, _, err := key.GetStringValue(name); err == nil && current == value {
		return nil
	}
	return key.SetStringValue(name, value)
}

func (windowsOutlookPlatform) SetDefaultSignature(name string) error {
	var lastErr error
	for _, version := range installedOfficeVersions() {
		key, _, err := registry.CreateKey(registry.CURRENT_USER,
			`Software\Microsoft\Office\`+version+`\Common\MailSettings`, registry.QUERY_VALUE|registry.SET_VALUE)
		if err != nil {
			lastErr = err
			continue
		}
		for _, valueName := range []string{"NewSignature", "ReplySignature"} {
			if current, _, err := key.GetStringValue(valueName); err == nil && current == name {
				continue
			}
			if err := key.SetExpandStringValue(valueName, name); err != nil {
				lastErr = err
			}
		}
		key.Close()
	}
	return lastErr
}

// DisableRoamingSignatures sets the per-user switches Microsoft documents for
// tools that manage signature files, so Outlook does not replace them with
// cloud-stored signatures.
func (windowsOutlookPlatform) DisableRoamingSignatures() error {
	key, _, err := registry.CreateKey(registry.CURRENT_USER,
		`Software\Microsoft\Office\16.0\Outlook\Setup`, registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer key.Close()
	for _, valueName := range []string{"DisableRoamingSignaturesTemporaryToggle", "DisableRoamingSignatures"} {
		if current, _, err := key.GetIntegerValue(valueName); err == nil && current == 1 {
			continue
		}
		if err := key.SetDWordValue(valueName, 1); err != nil {
			return fmt.Errorf("%s: %w", valueName, err)
		}
	}
	return nil
}

func (windowsOutlookPlatform) StateDir() (string, error) {
	base, err := os.UserCacheDir() // %LOCALAPPDATA%
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "MyPortal", "tray"), nil
}

// installedOfficeVersions returns the Office versions that have an Outlook
// key for the user, falling back to 16.0.
func installedOfficeVersions() []string {
	var versions []string
	for _, version := range outlookOfficeVersions {
		key, err := registry.OpenKey(registry.CURRENT_USER,
			`Software\Microsoft\Office\`+version+`\Outlook`, registry.QUERY_VALUE)
		if err != nil {
			continue
		}
		key.Close()
		versions = append(versions, version)
	}
	if len(versions) == 0 {
		versions = []string{"16.0"}
	}
	return versions
}
