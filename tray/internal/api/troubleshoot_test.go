package api_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestPostTroubleshootStatus(t *testing.T) {
	var gotPath, gotAuth, gotStage, gotCommand, gotMessage string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		if err := r.ParseForm(); err != nil {
			t.Errorf("ParseForm: %v", err)
		}
		gotStage = r.PostForm.Get("stage")
		gotCommand = r.PostForm.Get("command_id")
		gotMessage = r.PostForm.Get("message")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	if err := c.PostTroubleshootStatus(context.Background(), 42, 7, "received", "started", "ws-01"); err != nil {
		t.Fatalf("PostTroubleshootStatus: %v", err)
	}
	if gotPath != "/api/tickets/42/troubleshoot-status" {
		t.Errorf("path = %q", gotPath)
	}
	if gotAuth != "Bearer tok" {
		t.Errorf("auth = %q", gotAuth)
	}
	if gotStage != "received" || gotCommand != "7" || gotMessage != "started" {
		t.Errorf("form = stage %q command %q message %q", gotStage, gotCommand, gotMessage)
	}
}

func TestPostTroubleshootStatusHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	if err := c.PostTroubleshootStatus(context.Background(), 1, 1, "analysing", "", ""); err == nil {
		t.Fatal("expected an error for HTTP 409")
	}
}

func TestPostTroubleshootCompleteSendsError(t *testing.T) {
	var gotError, gotGuidance string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Errorf("ParseMultipartForm: %v", err)
		}
		gotError = r.FormValue("error")
		gotGuidance = r.FormValue("guidance")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	if err := c.PostTroubleshootComplete(context.Background(), 3, 9, "", "ws-01", "llm: connection refused", nil); err != nil {
		t.Fatalf("PostTroubleshootComplete: %v", err)
	}
	if gotError != "llm: connection refused" {
		t.Errorf("error field = %q", gotError)
	}
	if gotGuidance != "" {
		t.Errorf("guidance = %q", gotGuidance)
	}
}
