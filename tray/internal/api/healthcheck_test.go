package api_test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestPostHealthCheckStatus(t *testing.T) {
	var gotPath, gotAuth, gotStage, gotCommand string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		if err := r.ParseForm(); err != nil {
			t.Errorf("ParseForm: %v", err)
		}
		gotStage = r.PostForm.Get("stage")
		gotCommand = r.PostForm.Get("command_id")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	if err := c.PostHealthCheckStatus(context.Background(), 12, 34, "reading_sensors", "Installing PawnIO"); err != nil {
		t.Fatalf("PostHealthCheckStatus: %v", err)
	}
	if gotPath != "/api/health-checks/12/device-status" || gotAuth != "Bearer tok" || gotStage != "reading_sensors" || gotCommand != "34" {
		t.Errorf("path %q auth %q stage %q command %q", gotPath, gotAuth, gotStage, gotCommand)
	}
}

func TestPostHealthCheckResultUploadsSnapshotAndLogs(t *testing.T) {
	got := map[string]string{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/health-checks/12/device-result" {
			t.Errorf("path %q", r.URL.Path)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Errorf("ParseMultipartForm: %v", err)
		}
		for _, field := range []string{"command_id", "phase", "error"} {
			got[field] = r.FormValue(field)
		}
		for _, field := range []string{"snapshot", "log_bundle"} {
			file, _, err := r.FormFile(field)
			if err != nil {
				t.Errorf("%s: %v", field, err)
				continue
			}
			data, _ := io.ReadAll(file)
			got[field] = string(data)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	err := c.PostHealthCheckResult(context.Background(), 12, 34, "snapshot", []byte(`{"schema":1}`), []byte("gz"), "partial")
	if err != nil {
		t.Fatalf("PostHealthCheckResult: %v", err)
	}
	if got["command_id"] != "34" || got["phase"] != "snapshot" || got["error"] != "partial" ||
		got["snapshot"] != `{"schema":1}` || got["log_bundle"] != "gz" {
		t.Errorf("fields %v", got)
	}
}

func TestPostHealthCheckResultHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
	}))
	defer srv.Close()
	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	if err := c.PostHealthCheckResult(context.Background(), 1, 1, "logs", nil, []byte("gz"), ""); err == nil {
		t.Fatal("expected an error for HTTP 409")
	}
}
