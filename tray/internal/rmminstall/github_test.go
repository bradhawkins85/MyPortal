package rmminstall

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGitHubReleaseAssetsAndNoCredentials(t *testing.T) {
	for _, platform := range []struct{ os, arch string }{
		{"windows", "amd64"}, {"windows", "arm64"}, {"darwin", "amd64"}, {"darwin", "arm64"}, {"linux", "amd64"}, {"linux", "arm64"},
	} {
		for _, tag := range []string{"v1.2.3", "1.2.3"} {
			t.Run(platform.os+"/"+platform.arch+"/"+tag, func(t *testing.T) {
				name := "myportal-rmm-" + platform.os + "-" + platform.arch
				if platform.os == "windows" {
					name += ".exe"
				}
				body := []byte("release binary")
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Header.Get("Authorization") != "" || r.Header.Get("X-Tray-Token") != "" {
						t.Error("sent credentials to GitHub")
					}
					switch r.URL.Path {
					case "/releases/download/" + tag + "/" + name + ".sha256":
						fmt.Fprintf(w, "%s  %s\n", digest(body), name)
					case "/releases/download/" + tag + "/" + name:
						_, _ = w.Write(body)
					default:
						http.NotFound(w, r)
					}
				}))
				defer server.Close()
				g := &githubSource{baseURL: server.URL + "/releases/download/", client: server.Client()}
				pkg, err := g.GetRMMAgentPackage(context.Background(), platform.os, platform.arch, "v1.2.3")
				if err != nil {
					t.Fatal(err)
				}
				var out bytes.Buffer
				if err := g.DownloadRMMAgent(context.Background(), pkg.DownloadPath, &out); err != nil {
					t.Fatal(err)
				}
				if pkg.Version != "1.2.3" || pkg.Sha256 != digest(body) || out.String() != string(body) {
					t.Fatalf("package=%+v body=%q", pkg, out.String())
				}
			})
		}
	}
}

func TestGitHubRejectsUnusableChecksums(t *testing.T) {
	for _, body := range []string{"", "bad", strings.Repeat("z", 64), digest([]byte("agent")) + "  other-agent", strings.Repeat("x", 4097)} {
		t.Run(fmt.Sprintf("length%d", len(body)), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = fmt.Fprint(w, body) }))
			defer server.Close()
			g := &githubSource{baseURL: server.URL + "/", client: server.Client()}
			if _, err := g.GetRMMAgentPackage(context.Background(), "windows", "amd64", "1.2.3"); err == nil {
				t.Fatal("accepted invalid checksum")
			}
		})
	}
}

func TestGitHubRejectsInvalidVersionsAndPlatformsWithoutHTTP(t *testing.T) {
	g := &githubSource{}
	for _, version := range []string{"", "main", "../../1.2.3", "1.2.3?token=t", "1.2"} {
		if _, err := g.GetRMMAgentPackage(context.Background(), "windows", "amd64", version); err == nil {
			t.Fatalf("accepted %q", version)
		}
	}
	if _, err := g.GetRMMAgentPackage(context.Background(), "windows", "386", "1.2.3"); err == nil {
		t.Fatal("accepted unsupported platform")
	}
}

func TestGitHubHTTPFailures(t *testing.T) {
	for _, status := range []int{http.StatusForbidden, http.StatusNotFound, http.StatusTooManyRequests, http.StatusServiceUnavailable} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status) }))
			defer server.Close()
			g := &githubSource{baseURL: server.URL + "/", client: server.Client()}
			if _, err := g.GetRMMAgentPackage(context.Background(), "darwin", "arm64", "1.2.3"); err == nil {
				t.Fatal("accepted HTTP error for checksum")
			}
			if err := g.DownloadRMMAgent(context.Background(), g.baseURL+"v1.2.3/agent", &bytes.Buffer{}); err == nil {
				t.Fatal("accepted HTTP error for binary")
			}
			if err := g.DownloadRMMAgent(context.Background(), "https://other.example/agent", &bytes.Buffer{}); err == nil {
				t.Fatal("accepted another host")
			}
		})
	}
}
