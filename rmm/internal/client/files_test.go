package client

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestFileDownloadsAuthenticateAndRejectErrorsAndRedirects(t *testing.T) {
	status := http.StatusOK
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path != "/api/rmm/agent/runs/7/files/2" || r.Header.Get("Authorization") != "Bearer device-token" {
			t.Errorf("unexpected download request: %s %s", r.URL.Path, r.Header.Get("Authorization"))
		}
		if status == http.StatusFound {
			w.Header().Set("Location", "/redirected")
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte("binary\x00\xff"))
	}))
	defer server.Close()
	api := New(server.URL, "device-token")
	var out bytes.Buffer
	if err := api.DownloadRunFile(context.Background(), 7, 2, &out); err != nil || out.String() != "binary\x00\xff" {
		t.Fatalf("download: %v %q", err, out.String())
	}
	for _, code := range []int{http.StatusUnauthorized, http.StatusNotFound, http.StatusBadGateway, http.StatusFound} {
		status = code
		out.Reset()
		if err := api.DownloadRunFile(context.Background(), 7, 2, &out); err == nil {
			t.Fatalf("HTTP %d accepted", code)
		}
		if out.Len() != 0 {
			t.Fatal("HTTP error body written to file")
		}
	}
	if requests != 5 {
		t.Fatal("download followed a redirect")
	}
}
