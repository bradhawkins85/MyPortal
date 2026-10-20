package rmminstall

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

const githubReleaseURL = "https://github.com/bradhawkins85/MyPortal/releases/download/"
const maxAgentBytes = 256 << 20

var releaseVersion = regexp.MustCompile(`^[0-9]+(\.[0-9]+){2,3}$`)

// githubSource uses public release assets, never the portal client or device token.
type githubSource struct {
	baseURL string
	client  *http.Client
}

func newGitHubSource() *githubSource {
	return &githubSource{baseURL: githubReleaseURL, client: &http.Client{Timeout: 4 * time.Minute}}
}

func (g *githubSource) GetRMMAgentPackage(ctx context.Context, goos, goarch string, versions ...string) (*api.RMMAgentPackage, error) {
	if len(versions) == 0 || versions[0] == "" {
		return nil, errors.New("a matching release version is required for GitHub")
	}
	version := strings.TrimPrefix(strings.TrimSpace(versions[0]), "v")
	if !releaseVersion.MatchString(version) {
		return nil, fmt.Errorf("invalid RMM release version %q", versions[0])
	}
	if (goos != "windows" && goos != "darwin" && goos != "linux") || (goarch != "amd64" && goarch != "arm64") {
		return nil, fmt.Errorf("unsupported RMM platform %s/%s", goos, goarch)
	}
	name := "myportal-rmm-" + goos + "-" + goarch
	if goos == "windows" {
		name += ".exe"
	}
	// Releases use v-prefixed tags today; support older bare numeric tags too.
	for _, tag := range []string{"v" + version, version} {
		assetURL := g.baseURL + tag + "/" + name
		checksumCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		resp, err := g.get(checksumCtx, assetURL+".sha256")
		if err != nil {
			cancel()
			return nil, err
		}
		if resp.StatusCode == http.StatusNotFound {
			resp.Body.Close()
			cancel()
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 4097))
		resp.Body.Close()
		cancel()
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("GitHub checksum: HTTP %d", resp.StatusCode)
		}
		if readErr != nil {
			return nil, readErr
		}
		fields := strings.Fields(string(body))
		if len(body) > 4096 || len(fields) == 0 {
			return nil, errors.New("invalid GitHub SHA-256 file")
		}
		digest, err := hex.DecodeString(fields[0])
		if err != nil || len(digest) != 32 {
			return nil, errors.New("invalid GitHub SHA-256")
		}
		if len(fields) > 1 && strings.TrimPrefix(fields[1], "*") != name {
			return nil, errors.New("GitHub checksum names a different agent")
		}
		return &api.RMMAgentPackage{FileName: name, Version: version, Sha256: fields[0], DownloadPath: assetURL}, nil
	}
	return nil, fmt.Errorf("GitHub has no RMM build for %s/%s version %s", goos, goarch, version)
}

func (g *githubSource) get(ctx context.Context, url string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	return g.client.Do(req)
}

func (g *githubSource) DownloadRMMAgent(ctx context.Context, downloadURL string, w io.Writer) error {
	if !strings.HasPrefix(downloadURL, g.baseURL) {
		return errors.New("unexpected GitHub agent URL")
	}
	resp, err := g.get(ctx, downloadURL)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GitHub agent download: HTTP %d", resp.StatusCode)
	}
	n, err := io.Copy(w, io.LimitReader(resp.Body, maxAgentBytes+1))
	if err != nil {
		return err
	}
	if n > maxAgentBytes {
		return fmt.Errorf("GitHub agent download: larger than %d bytes", maxAgentBytes)
	}
	return nil
}
