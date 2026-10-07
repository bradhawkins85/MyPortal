// outlook_signatures.go keeps Classic Outlook signatures in sync with the
// signature template published in MyPortal. It is compiled into both the
// webview and nowebview builds (no build tag).
//
// Classic Outlook for Windows stores signatures as files in the user's
// profile and selects defaults through HKCU registry values, so this worker
// runs in the per-user UI agent rather than the SYSTEM service. On each cycle
// it:
//
//  1. discovers the email addresses of the user's Outlook accounts;
//  2. asks the portal to render the signatures available to each of them
//     (the portal returns nothing unless the company opted in);
//  3. writes the .htm, .rtf and .txt signature files when their content
//     changed, saving images embedded as base64 data: URIs into the
//     "<name>_files" folder Outlook expects;
//  4. makes each account's primary signature the default for new messages
//     and replies, leaving additional signatures available in the picker; and
//  5. removes additional signatures it wrote earlier that no longer apply.
//
// Existing signatures created by the user are never modified or deleted.
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const (
	outlookSignatureInterval     = time.Hour
	outlookSignatureStartupDelay = 45 * time.Second
	outlookSignatureStateName    = "outlook-signatures.json"
	maxOutlookSignatureAddresses = 20
)

// outlookAccount is one mail account configured in a Classic Outlook profile.
type outlookAccount struct {
	// Key identifies the account's registry key so its defaults can be set.
	Key     string
	Address string
}

// outlookPlatform isolates the Windows-specific file locations and registry
// access so the sync logic can be tested on any platform.
type outlookPlatform interface {
	Supported() bool
	Accounts() ([]outlookAccount, error)
	SignaturesDir() (string, error)
	// SetAccountSignature makes name the new-message and reply signature for
	// the account.
	SetAccountSignature(account outlookAccount, name string) error
	// SetDefaultSignature sets the signature Outlook assigns to accounts that
	// are added later.
	SetDefaultSignature(name string) error
	// DisableRoamingSignatures stops Outlook's cloud signature sync from
	// replacing the files written by this worker.
	DisableRoamingSignatures() error
	StateDir() (string, error)
}

type outlookSignature struct {
	Address string `json:"address"`
	Name    string `json:"name"`
	HTML    string `json:"html"`
	Text    string `json:"text"`
	Hash    string `json:"hash"`
}

type outlookSignaturesResponse struct {
	Enabled      bool   `json:"enabled"`
	TemplateSlug string `json:"template_slug"`
	// Signatures holds each account's primary signature.
	Signatures []outlookSignature `json:"signatures"`
	// AdditionalSignatures are available to the account but never made the
	// default.
	AdditionalSignatures []outlookSignature `json:"additional_signatures"`
	Skipped              map[string]string  `json:"skipped"`
}

// outlookSignatureState records the content hash written for each signature
// file so unchanged signatures are not rewritten every hour, and which
// additional signatures were written for which address so they can be
// removed once they no longer apply.
type outlookSignatureState struct {
	Hashes     map[string]string `json:"hashes"`
	Additional map[string]string `json:"additional,omitempty"`
}

var (
	outlookSignatureSyncMu  sync.Mutex
	outlookSignatureTrigger = make(chan struct{}, 1)
	outlookSignatureHTTP    = &http.Client{Timeout: 30 * time.Second}
)

// runOutlookSignatureWorker syncs signatures shortly after start-up, then
// hourly and whenever the service reports a configuration change.
func runOutlookSignatureWorker() {
	platform := newOutlookPlatform()
	if !platform.Supported() {
		return
	}
	timer := time.NewTimer(outlookSignatureStartupDelay)
	defer timer.Stop()
	for {
		select {
		case <-timer.C:
		case <-outlookSignatureTrigger:
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
		}
		if err := syncOutlookSignatures(context.Background(), platform, gPortalURL, gAuthToken); err != nil {
			logger.Warn("Outlook signatures: %v", err)
		}
		timer.Reset(outlookSignatureInterval)
	}
}

// triggerOutlookSignatureSync requests an immediate sync without blocking.
func triggerOutlookSignatureSync() {
	select {
	case outlookSignatureTrigger <- struct{}{}:
	default:
	}
}

func syncOutlookSignatures(ctx context.Context, platform outlookPlatform, portalURL, authToken string) error {
	outlookSignatureSyncMu.Lock()
	defer outlookSignatureSyncMu.Unlock()

	if portalURL == "" || authToken == "" {
		logger.Debug("Outlook signatures: portal URL or auth token not available yet")
		return nil
	}
	accounts, err := platform.Accounts()
	if err != nil {
		return fmt.Errorf("discover Outlook accounts: %w", err)
	}
	addresses := uniqueAddresses(accounts)
	if len(addresses) == 0 {
		logger.Debug("Outlook signatures: no Classic Outlook accounts found")
		return nil
	}

	response, err := fetchOutlookSignatures(ctx, portalURL, authToken, addresses)
	if err != nil {
		return err
	}
	if !response.Enabled {
		logger.Debug("Outlook signatures: not enabled for this company")
		return nil
	}
	for address, reason := range response.Skipped {
		logger.Info("Outlook signatures: skipped %s: %s", address, reason)
	}
	state := loadOutlookSignatureState(platform)
	if len(response.Signatures) == 0 && len(response.AdditionalSignatures) == 0 {
		if dir, err := platform.SignaturesDir(); err == nil &&
			pruneAdditionalSignatures(dir, &state, addresses, response, nil) > 0 {
			saveOutlookSignatureState(platform, state)
		}
		return nil
	}

	if err := platform.DisableRoamingSignatures(); err != nil {
		logger.Warn("Outlook signatures: disable roaming signatures: %v", err)
	}
	dir, err := platform.SignaturesDir()
	if err != nil {
		return fmt.Errorf("locate signatures folder: %w", err)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create signatures folder: %w", err)
	}

	written := 0
	var defaultName string
	for _, signature := range response.Signatures {
		base := signatureFileBase(signature.Name)
		if base == "" {
			logger.Warn("Outlook signatures: ignoring signature with an unusable name for %s", signature.Address)
			continue
		}
		if state.Hashes[base] != signature.Hash || !signatureFilesExist(dir, base, signature) {
			if err := writeSignatureFiles(dir, base, signature); err != nil {
				logger.Warn("Outlook signatures: write %s: %v", base, err)
				continue
			}
			state.Hashes[base] = signature.Hash
			written++
		}
		if defaultName == "" {
			defaultName = base
		}
		for _, account := range accounts {
			if !strings.EqualFold(account.Address, signature.Address) {
				continue
			}
			if err := platform.SetAccountSignature(account, base); err != nil {
				logger.Warn("Outlook signatures: set default for %s: %v", account.Address, err)
			}
		}
	}
	if defaultName != "" {
		if err := platform.SetDefaultSignature(defaultName); err != nil {
			logger.Warn("Outlook signatures: set default for new accounts: %v", err)
		}
	}
	current := map[string]bool{}
	for _, signature := range response.AdditionalSignatures {
		base := signatureFileBase(signature.Name)
		if base == "" {
			logger.Warn("Outlook signatures: ignoring signature with an unusable name for %s", signature.Address)
			continue
		}
		current[base] = true
		state.Additional[base] = strings.ToLower(strings.TrimSpace(signature.Address))
		if state.Hashes[base] != signature.Hash || !signatureFilesExist(dir, base, signature) {
			if err := writeSignatureFiles(dir, base, signature); err != nil {
				logger.Warn("Outlook signatures: write %s: %v", base, err)
				continue
			}
			state.Hashes[base] = signature.Hash
			written++
		}
	}
	removed := pruneAdditionalSignatures(dir, &state, addresses, response, current)
	saveOutlookSignatureState(platform, state)
	logger.Info("Outlook signatures: template %q applied to %d account(s), %d additional signature(s), %d file set(s) updated, %d removed",
		response.TemplateSlug, len(response.Signatures), len(current), written, removed)
	return nil
}

// pruneAdditionalSignatures deletes additional signatures written by an
// earlier sync that the portal no longer returns. Only addresses the portal
// answered for in this sync are considered: a skipped address may just be
// temporarily unmatched, so its signatures are left alone.
func pruneAdditionalSignatures(dir string, state *outlookSignatureState, requested []string,
	response *outlookSignaturesResponse, current map[string]bool) int {
	answered := map[string]bool{}
	for _, address := range requested {
		answered[strings.ToLower(address)] = true
	}
	for address := range response.Skipped {
		delete(answered, strings.ToLower(strings.TrimSpace(address)))
	}
	removed := 0
	for base, address := range state.Additional {
		if current[base] || !answered[address] {
			continue
		}
		failed := false
		for _, ext := range []string{".htm", ".rtf", ".txt"} {
			if err := os.Remove(filepath.Join(dir, base+ext)); err != nil && !os.IsNotExist(err) {
				logger.Warn("Outlook signatures: remove %s%s: %v", base, ext, err)
				failed = true
			}
		}
		// Outlook keeps images and other assets for an HTML signature in a
		// "<name>_files" folder next to it.
		_ = os.RemoveAll(filepath.Join(dir, base+"_files"))
		if failed {
			continue
		}
		delete(state.Additional, base)
		delete(state.Hashes, base)
		removed++
	}
	return removed
}

func uniqueAddresses(accounts []outlookAccount) []string {
	seen := map[string]bool{}
	var addresses []string
	for _, account := range accounts {
		address := strings.TrimSpace(account.Address)
		key := strings.ToLower(address)
		if address == "" || !strings.Contains(address, "@") || seen[key] {
			continue
		}
		seen[key] = true
		addresses = append(addresses, address)
		if len(addresses) == maxOutlookSignatureAddresses {
			break
		}
	}
	return addresses
}

func fetchOutlookSignatures(ctx context.Context, portalURL, authToken string, addresses []string) (*outlookSignaturesResponse, error) {
	body, err := json.Marshal(map[string][]string{"addresses": addresses})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		strings.TrimRight(portalURL, "/")+"/api/tray/outlook-signatures", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+authToken)
	resp, err := outlookSignatureHTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request signatures: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		// Older portal without this endpoint.
		return &outlookSignaturesResponse{}, nil
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("request signatures: HTTP %d", resp.StatusCode)
	}
	var out outlookSignaturesResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode signatures: %w", err)
	}
	return &out, nil
}

// signatureFileBase converts a signature name into a safe file name without
// an extension. Outlook shows this base name in its signature picker.
func signatureFileBase(name string) string {
	var b strings.Builder
	for _, r := range strings.TrimSpace(name) {
		switch {
		case r < 32 || strings.ContainsRune(`<>:"/\|?*`, r):
			b.WriteRune('_')
		default:
			b.WriteRune(r)
		}
	}
	base := strings.TrimRight(b.String(), ". ")
	if len(base) > 120 {
		base = base[:120]
	}
	return base
}

// signatureFilesExist reports whether every file for the signature is on
// disk, including each image extracted into "<base>_files". This also
// rewrites signatures saved by agents that predate image extraction even
// though their content hash is unchanged.
func signatureFilesExist(dir, base string, signature outlookSignature) bool {
	for _, ext := range []string{".htm", ".rtf", ".txt"} {
		if _, err := os.Stat(filepath.Join(dir, base+ext)); err != nil {
			return false
		}
	}
	_, images := extractSignatureImages(signature.HTML, base)
	for _, image := range images {
		if _, err := os.Stat(filepath.Join(dir, base+"_files", image.Name)); err != nil {
			return false
		}
	}
	return true
}

func writeSignatureFiles(dir, base string, signature outlookSignature) error {
	html, images := extractSignatureImages(signature.HTML, base)
	if err := writeSignatureImages(dir, base, images); err != nil {
		return err
	}
	files := map[string][]byte{
		".htm": signatureHTMLDocument(html),
		".rtf": []byte(signatureRTF(signature.Text)),
		".txt": utf16LEWithBOM(crlf(signature.Text)),
	}
	for _, ext := range []string{".htm", ".rtf", ".txt"} {
		if err := writeFileAtomic(filepath.Join(dir, base+ext), files[ext]); err != nil {
			return err
		}
	}
	return nil
}

// signatureImage is an image saved next to a signature's .htm file.
type signatureImage struct {
	Name string
	Data []byte
}

// signatureDataImagePattern matches an <img> src holding a base64 data: URI.
// The portal embeds signature images this way, but Classic Outlook (Word's
// HTML engine) does not reliably render or send data: URIs, so they are
// written out as files instead.
var signatureDataImagePattern = regexp.MustCompile(
	`(?i)(<img\b[^>]*?\ssrc\s*=\s*)(["'])data:image/(png|jpe?g|gif|bmp|webp);base64,([A-Za-z0-9+/=\s]+)(["'])`)

// signatureFolderURL escapes a signature folder name for a relative URL the
// way Outlook writes it (e.g. "My%20Sig_files"). signatureFileBase already
// strips characters that are invalid in file names.
var signatureFolderURL = strings.NewReplacer("%", "%25", " ", "%20", "#", "%23", "'", "%27")

var signatureImageExtensions = map[string]string{
	"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "gif": ".gif", "bmp": ".bmp", "webp": ".webp",
}

// extractSignatureImages replaces data: URI images with references to files
// in the "<base>_files" folder, the layout Outlook itself uses for HTML
// signatures. Identical images share one file.
func extractSignatureImages(html, base string) (string, []signatureImage) {
	matches := signatureDataImagePattern.FindAllStringSubmatchIndex(html, -1)
	if len(matches) == 0 {
		return html, nil
	}
	folder := signatureFolderURL.Replace(base + "_files")
	var images []signatureImage
	byContent := map[string]string{}
	var b strings.Builder
	last := 0
	for _, m := range matches {
		openQuote, closeQuote := html[m[4]:m[5]], html[m[10]:m[11]]
		encoded := strings.Join(strings.Fields(html[m[8]:m[9]]), "")
		data, err := base64.StdEncoding.DecodeString(encoded)
		if openQuote != closeQuote || err != nil || len(data) == 0 {
			continue
		}
		name, seen := byContent[string(data)]
		if !seen {
			ext := signatureImageExtensions[strings.ToLower(html[m[6]:m[7]])]
			name = fmt.Sprintf("image%03d%s", len(images)+1, ext)
			byContent[string(data)] = name
			images = append(images, signatureImage{Name: name, Data: data})
		}
		b.WriteString(html[last:m[0]])
		b.WriteString(html[m[2]:m[3]])
		b.WriteString(openQuote + folder + "/" + name + closeQuote)
		last = m[1]
	}
	b.WriteString(html[last:])
	return b.String(), images
}

// writeSignatureImages replaces the "<base>_files" folder with the given
// images, removing it when the signature has none.
func writeSignatureImages(dir, base string, images []signatureImage) error {
	folder := filepath.Join(dir, base+"_files")
	if err := os.RemoveAll(folder); err != nil {
		return err
	}
	if len(images) == 0 {
		return nil
	}
	if err := os.MkdirAll(folder, 0o700); err != nil {
		return err
	}
	for _, image := range images {
		if err := writeFileAtomic(filepath.Join(folder, image.Name), image.Data); err != nil {
			return err
		}
	}
	return nil
}

// signatureHTMLDocument wraps the sanitised fragment from the portal in the
// document structure Outlook expects, declaring UTF-8 so non-ASCII text
// renders correctly.
func signatureHTMLDocument(fragment string) []byte {
	var b bytes.Buffer
	b.Write([]byte{0xEF, 0xBB, 0xBF})
	b.WriteString("<html>\r\n<head>\r\n")
	b.WriteString(`<meta http-equiv="Content-Type" content="text/html; charset=utf-8">` + "\r\n")
	b.WriteString(`<meta name="Generator" content="MyPortal">` + "\r\n")
	b.WriteString("</head>\r\n<body>\r\n")
	b.WriteString(fragment)
	b.WriteString("\r\n</body>\r\n</html>\r\n")
	return b.Bytes()
}

// signatureRTF renders the plain-text signature for messages composed in
// Rich Text format. Non-ASCII characters use RTF Unicode escapes.
func signatureRTF(text string) string {
	var b strings.Builder
	b.WriteString(`{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss Calibri;}}\f0\fs22 `)
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	for i, line := range lines {
		if i > 0 {
			b.WriteString(`\par `)
		}
		for _, unit := range utf16.Encode([]rune(line)) {
			switch {
			case unit == '\\' || unit == '{' || unit == '}':
				b.WriteByte('\\')
				b.WriteByte(byte(unit))
			case unit >= 0x20 && unit < 0x80:
				b.WriteByte(byte(unit))
			default:
				// \uN takes a signed 16-bit value followed by a fallback character.
				fmt.Fprintf(&b, `\u%d?`, int16(unit))
			}
		}
	}
	b.WriteString(`\par }`)
	return b.String()
}

func crlf(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\n", "\r\n")
}

func utf16LEWithBOM(text string) []byte {
	units := utf16.Encode([]rune(text))
	out := make([]byte, 2+2*len(units))
	out[0], out[1] = 0xFF, 0xFE
	for i, unit := range units {
		binary.LittleEndian.PutUint16(out[2+2*i:], unit)
	}
	return out
}

func writeFileAtomic(path string, data []byte) error {
	tmp := path + ".myportal-tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func loadOutlookSignatureState(platform outlookPlatform) outlookSignatureState {
	state := outlookSignatureState{Hashes: map[string]string{}, Additional: map[string]string{}}
	dir, err := platform.StateDir()
	if err != nil {
		return state
	}
	data, err := os.ReadFile(filepath.Join(dir, outlookSignatureStateName))
	if err != nil {
		return state
	}
	if err := json.Unmarshal(data, &state); err != nil || state.Hashes == nil {
		return outlookSignatureState{Hashes: map[string]string{}, Additional: map[string]string{}}
	}
	if state.Additional == nil {
		state.Additional = map[string]string{}
	}
	return state
}

func saveOutlookSignatureState(platform outlookPlatform, state outlookSignatureState) {
	dir, err := platform.StateDir()
	if err != nil {
		return
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		logger.Warn("Outlook signatures: create state folder: %v", err)
		return
	}
	data, _ := json.Marshal(state)
	if err := writeFileAtomic(filepath.Join(dir, outlookSignatureStateName), data); err != nil {
		logger.Warn("Outlook signatures: save state: %v", err)
	}
}
