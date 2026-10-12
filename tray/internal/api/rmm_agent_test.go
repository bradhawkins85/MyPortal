package api

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRMMAgentDownloadSendsTheTokenOnlyToMyPortalPaths(t *testing.T) {
	var auth, query string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth, query = r.Header.Get("Authorization"), r.URL.RawQuery
		switch r.URL.Path {
		case "/api/rmm/agent/package":
			_, _ = io.WriteString(w, `{"version":"1.0","sha256":"ab","download_path":"/api/rmm/agent/package/download?os=windows&arch=amd64"}`)
		case "/api/rmm/agent/package/download":
			_, _ = io.WriteString(w, "binary")
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	c := New(srv.URL)
	c.SetAuth("uid", "tok")

	pkg, err := c.GetRMMAgentPackage(context.Background(), "windows", "amd64")
	if err != nil || pkg.Version != "1.0" || query != "arch=amd64&os=windows" {
		t.Fatalf("package = %+v, %v (query %q)", pkg, err, query)
	}
	var buf bytes.Buffer
	if err := c.DownloadRMMAgent(context.Background(), pkg.DownloadPath, &buf); err != nil || buf.String() != "binary" || auth != "Bearer tok" {
		t.Fatalf("download = %q, %v, auth %q", buf.String(), err, auth)
	}
	for _, path := range []string{"//evil.example.com/x", "/api/tray/config", "https://evil.example.com/api/rmm/agent/package/download?"} {
		if err := c.DownloadRMMAgent(context.Background(), path, &buf); err == nil || !strings.Contains(err.Error(), "unexpected path") {
			t.Fatalf("%q: err = %v", path, err)
		}
	}
}

func TestRMMAgentPackageRequestsMatchingRelease(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("version") != "v1.2.3" {
			t.Errorf("version query = %q", r.URL.Query().Get("version"))
		}
		_, _ = io.WriteString(w, `{"version":"1.2.3"}`)
	}))
	defer srv.Close()
	pkg, err := New(srv.URL).GetRMMAgentPackage(context.Background(), "windows", "amd64", "v1.2.3")
	if err != nil || pkg.Version != "1.2.3" {
		t.Fatalf("package = %+v, %v", pkg, err)
	}
}
