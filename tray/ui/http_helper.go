// http_helper.go provides a shared HTTP client used by the tray UI to request
// one-time chat tokens from the portal.  It is compiled into both the webview
// and nowebview builds (no build tag).
package main

import (
	"bytes"
	"net/http"
	"time"
)

// httpClient is a shared HTTP client used for API calls from within the UI
// process.  The timeout is kept short (5 s) so that user-facing actions such
// as opening the chat window do not stall for a long time when the portal is
// temporarily unreachable.
var httpClient = &http.Client{Timeout: 5 * time.Second}

// newHTTPRequest creates a POST request with the device auth token in the
// Authorization header and a JSON body.
func newHTTPRequest(method, url string, body []byte) (*http.Request, error) {
	req, err := http.NewRequest(method, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if gAuthToken != "" {
		req.Header.Set("Authorization", "Bearer "+gAuthToken)
	}
	return req, nil
}
