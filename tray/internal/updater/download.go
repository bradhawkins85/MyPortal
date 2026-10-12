package updater

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

// maxInstallerBytes bounds the installer download. The Windows MSI carries
// the chat shell's Electron runtime, so it can be well over 100 MB.
const maxInstallerBytes = 1 << 30

// downloadClient allows slow links time to fetch a large installer.
var downloadClient = &http.Client{Timeout: 30 * time.Minute}

func downloadFile(ctx context.Context, rawURL, dest string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return err
	}
	resp, err := downloadClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download: HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer f.Close()
	n, err := io.Copy(f, io.LimitReader(resp.Body, maxInstallerBytes+1))
	if err != nil {
		return err
	}
	if n > maxInstallerBytes {
		return fmt.Errorf("download: installer is larger than %d bytes", maxInstallerBytes)
	}
	return f.Close()
}
