package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeOutlookPlatform struct {
	accounts        []outlookAccount
	sigDir          string
	stateDir        string
	accountDefaults map[string]string
	defaultName     string
	roamingOff      bool
}

func (f *fakeOutlookPlatform) Supported() bool                     { return true }
func (f *fakeOutlookPlatform) Accounts() ([]outlookAccount, error) { return f.accounts, nil }
func (f *fakeOutlookPlatform) SignaturesDir() (string, error)      { return f.sigDir, nil }
func (f *fakeOutlookPlatform) StateDir() (string, error)           { return f.stateDir, nil }
func (f *fakeOutlookPlatform) SetDefaultSignature(name string) error {
	f.defaultName = name
	return nil
}
func (f *fakeOutlookPlatform) DisableRoamingSignatures() error { f.roamingOff = true; return nil }
func (f *fakeOutlookPlatform) SetAccountSignature(a outlookAccount, name string) error {
	f.accountDefaults[a.Key] = name
	return nil
}

func newFakePlatform(t *testing.T, accounts ...outlookAccount) *fakeOutlookPlatform {
	t.Helper()
	root := t.TempDir()
	return &fakeOutlookPlatform{
		accounts:        accounts,
		sigDir:          filepath.Join(root, "Signatures"),
		stateDir:        filepath.Join(root, "state"),
		accountDefaults: map[string]string{},
	}
}

func signaturePortal(t *testing.T, response outlookSignaturesResponse, requests *[][]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tray/outlook-signatures" || r.Header.Get("Authorization") != "Bearer token" {
			http.Error(w, "unexpected request", http.StatusBadRequest)
			return
		}
		var body struct {
			Addresses []string `json:"addresses"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		*requests = append(*requests, body.Addresses)
		_ = json.NewEncoder(w).Encode(response)
	}))
}

func TestSyncWritesSignatureFilesAndSetsDefaults(t *testing.T) {
	platform := newFakePlatform(t,
		outlookAccount{Key: "profile\\0001", Address: "Ada@Example.com"},
		outlookAccount{Key: "profile\\0002", Address: "ada@example.com"},
		outlookAccount{Key: "profile\\0003", Address: "personal@gmail.com"},
	)
	var requests [][]string
	server := signaturePortal(t, outlookSignaturesResponse{
		Enabled:      true,
		TemplateSlug: "standard",
		Signatures: []outlookSignature{{
			Address: "Ada@Example.com", Name: "MyPortal (Ada@Example.com)",
			HTML: "<p>Ada Lovelace — Engineer</p>", Text: "Ada Lovelace — Engineer\nPhone {1}", Hash: "h1",
		}},
		Skipped: map[string]string{"personal@gmail.com": "Address is not on a company email domain"},
	}, &requests)
	defer server.Close()

	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatalf("sync: %v", err)
	}

	if got := requests[0]; len(got) != 2 || got[0] != "Ada@Example.com" || got[1] != "personal@gmail.com" {
		t.Fatalf("addresses sent = %v, want de-duplicated account addresses", got)
	}
	base := "MyPortal (Ada@Example.com)"
	html, err := os.ReadFile(filepath.Join(platform.sigDir, base+".htm"))
	if err != nil {
		t.Fatalf("read htm: %v", err)
	}
	if !bytes.HasPrefix(html, []byte{0xEF, 0xBB, 0xBF}) || !strings.Contains(string(html), "charset=utf-8") ||
		!strings.Contains(string(html), "<p>Ada Lovelace — Engineer</p>") {
		t.Fatalf("unexpected htm content: %q", html)
	}
	txt, _ := os.ReadFile(filepath.Join(platform.sigDir, base+".txt"))
	if !bytes.Equal(txt[:2], []byte{0xFF, 0xFE}) || !bytes.Equal(txt[2:4], []byte{'A', 0}) {
		t.Fatalf("txt must be UTF-16LE with BOM, got % x", txt[:8])
	}
	rtf, _ := os.ReadFile(filepath.Join(platform.sigDir, base+".rtf"))
	if !strings.Contains(string(rtf), `Ada Lovelace \u8212? Engineer\par Phone \{1\}`) {
		t.Fatalf("unexpected rtf: %s", rtf)
	}
	if platform.accountDefaults["profile\\0001"] != base || platform.accountDefaults["profile\\0002"] != base {
		t.Fatalf("account defaults = %v", platform.accountDefaults)
	}
	if _, ok := platform.accountDefaults["profile\\0003"]; ok {
		t.Fatal("skipped personal account must not be changed")
	}
	if platform.defaultName != base || !platform.roamingOff {
		t.Fatalf("default=%q roamingOff=%v", platform.defaultName, platform.roamingOff)
	}
}

func TestSyncSkipsUnchangedFilesAndRestoresMissingOnes(t *testing.T) {
	platform := newFakePlatform(t, outlookAccount{Key: "k", Address: "ada@example.com"})
	var requests [][]string
	server := signaturePortal(t, outlookSignaturesResponse{
		Enabled: true,
		Signatures: []outlookSignature{{
			Address: "ada@example.com", Name: "MyPortal (ada@example.com)", HTML: "<p>v1</p>", Text: "v1", Hash: "same",
		}},
	}, &requests)
	defer server.Close()

	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	htm := filepath.Join(platform.sigDir, "MyPortal (ada@example.com).htm")
	if err := os.WriteFile(htm, []byte("user edit"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(htm); string(data) != "user edit" {
		t.Fatal("unchanged signature hash must not rewrite files")
	}
	_ = os.Remove(filepath.Join(platform.sigDir, "MyPortal (ada@example.com).txt"))
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if !signatureFilesExist(platform.sigDir, "MyPortal (ada@example.com)", outlookSignature{}) {
		t.Fatal("missing signature files must be recreated")
	}
}

func TestSyncDoesNothingWhenDisabledOrNoAccounts(t *testing.T) {
	var requests [][]string
	server := signaturePortal(t, outlookSignaturesResponse{Enabled: false}, &requests)
	defer server.Close()

	empty := newFakePlatform(t)
	if err := syncOutlookSignatures(context.Background(), empty, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if len(requests) != 0 {
		t.Fatal("no request should be made without Outlook accounts")
	}

	platform := newFakePlatform(t, outlookAccount{Key: "k", Address: "ada@example.com"})
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(platform.sigDir); !os.IsNotExist(err) || platform.roamingOff || len(platform.accountDefaults) != 0 {
		t.Fatal("disabled company must not change Outlook")
	}
}

func TestSignatureFileBaseRemovesInvalidCharacters(t *testing.T) {
	if got := signatureFileBase(` a/b\c:d*e?f"g<h>i|j. `); got != "a_b_c_d_e_f_g_h_i_j" {
		t.Fatalf("signatureFileBase = %q", got)
	}
	if got := signatureFileBase(" ... "); got != "" {
		t.Fatalf("expected empty base, got %q", got)
	}
}

func TestSyncWritesAdditionalSignaturesAndRemovesOnesThatNoLongerApply(t *testing.T) {
	platform := newFakePlatform(t,
		outlookAccount{Key: "k1", Address: "ada@example.com"},
		outlookAccount{Key: "k2", Address: "grace@example.com"},
	)
	response := outlookSignaturesResponse{
		Enabled: true,
		Signatures: []outlookSignature{{
			Address: "ada@example.com", Name: "MyPortal (ada@example.com)", HTML: "<p>main</p>", Text: "main", Hash: "p1",
		}},
		AdditionalSignatures: []outlookSignature{
			{Address: "ada@example.com", Name: "MyPortal - Short reply (ada@example.com)", HTML: "<p>short</p>", Text: "short", Hash: "a1"},
			{Address: "grace@example.com", Name: "MyPortal - Sales (grace@example.com)", HTML: "<p>sales</p>", Text: "sales", Hash: "a2"},
		},
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()

	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	for _, base := range []string{"MyPortal - Short reply (ada@example.com)", "MyPortal - Sales (grace@example.com)"} {
		if !signatureFilesExist(platform.sigDir, base, outlookSignature{}) {
			t.Fatalf("additional signature %q was not written", base)
		}
	}
	if platform.accountDefaults["k1"] != "MyPortal (ada@example.com)" || platform.defaultName != "MyPortal (ada@example.com)" {
		t.Fatalf("primary must stay the default: accounts=%v default=%q", platform.accountDefaults, platform.defaultName)
	}
	if _, ok := platform.accountDefaults["k2"]; ok {
		t.Fatal("an additional signature must never be made an account default")
	}

	// Grace is skipped this time (left alone) and Ada's short reply no
	// longer applies (removed).
	response.AdditionalSignatures = nil
	response.Skipped = map[string]string{"grace@example.com": "No active staff record matches this address"}
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(platform.sigDir, "MyPortal - Short reply (ada@example.com).htm")); !os.IsNotExist(err) {
		t.Fatal("additional signature that no longer applies must be removed")
	}
	if !signatureFilesExist(platform.sigDir, "MyPortal - Sales (grace@example.com)", outlookSignature{}) {
		t.Fatal("signatures for a skipped address must be left in place")
	}
	if !signatureFilesExist(platform.sigDir, "MyPortal (ada@example.com)", outlookSignature{}) {
		t.Fatal("primary signature must be kept")
	}

	// Nothing applies to anyone any more: Grace's signature goes too.
	response.Signatures = nil
	response.Skipped = nil
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(platform.sigDir, "MyPortal - Sales (grace@example.com).htm")); !os.IsNotExist(err) {
		t.Fatal("additional signatures must be removed when none apply")
	}
	if state := loadOutlookSignatureState(platform); len(state.Additional) != 0 {
		t.Fatalf("state still tracks removed signatures: %v", state.Additional)
	}
}

func TestExtractSignatureImagesWritesDataURIsAsFiles(t *testing.T) {
	png := []byte("\x89PNG\r\n\x1a\nfake")
	encoded := base64.StdEncoding.EncodeToString(png)
	html := `<p><img alt="Logo" src="data:image/png;base64,` + encoded + `"></p>` +
		`<p><img src='data:image/jpeg;base64,/9j/'></p>` +
		`<p><img src="data:image/png;base64,` + encoded + `" width="10"></p>` +
		`<p><a href="https://example.com">data:image/png;base64,AAAA</a></p>`

	out, images := extractSignatureImages(html, "My Sig")

	if len(images) != 2 || images[0].Name != "image001.png" || images[1].Name != "image002.jpg" {
		t.Fatalf("images = %+v", images)
	}
	if !bytes.Equal(images[0].Data, png) {
		t.Fatalf("decoded image = %q", images[0].Data)
	}
	if strings.Count(out, `src="My%20Sig_files/image001.png"`) != 2 ||
		!strings.Contains(out, `src='My%20Sig_files/image002.jpg'`) {
		t.Fatalf("unexpected html: %s", out)
	}
	if strings.Contains(out, "src=\"data:") || !strings.Contains(out, ">data:image/png;base64,AAAA</a>") {
		t.Fatalf("only img src attributes must be rewritten: %s", out)
	}
}

func TestSyncWritesEmbeddedImagesForClassicOutlook(t *testing.T) {
	platform := newFakePlatform(t, outlookAccount{Key: "profile\\0001", Address: "ada@example.com"})
	png := []byte("\x89PNG\r\n\x1a\nlogo")
	signature := outlookSignature{
		Address: "ada@example.com", Name: "MyPortal (ada@example.com)",
		HTML: `<p>Ada</p><img src="data:image/png;base64,` + base64.StdEncoding.EncodeToString(png) + `">`,
		Text: "Ada", Hash: "h1",
	}
	var requests [][]string
	server := signaturePortal(t, outlookSignaturesResponse{Enabled: true, Signatures: []outlookSignature{signature}}, &requests)
	defer server.Close()

	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatalf("sync: %v", err)
	}
	base := "MyPortal (ada@example.com)"
	htm, _ := os.ReadFile(filepath.Join(platform.sigDir, base+".htm"))
	if !strings.Contains(string(htm), `src="MyPortal%20(ada@example.com)_files/image001.png"`) {
		t.Fatalf("htm does not reference the image file: %s", htm)
	}
	written, err := os.ReadFile(filepath.Join(platform.sigDir, base+"_files", "image001.png"))
	if err != nil || !bytes.Equal(written, png) {
		t.Fatalf("image file = %q, %v", written, err)
	}

	// An agent that predates image extraction left no _files folder; the
	// unchanged hash must not stop the images from being written.
	if err := os.RemoveAll(filepath.Join(platform.sigDir, base+"_files")); err != nil {
		t.Fatal(err)
	}
	if err := syncOutlookSignatures(context.Background(), platform, server.URL, "token"); err != nil {
		t.Fatalf("second sync: %v", err)
	}
	if _, err := os.Stat(filepath.Join(platform.sigDir, base+"_files", "image001.png")); err != nil {
		t.Fatalf("image not restored: %v", err)
	}
}
