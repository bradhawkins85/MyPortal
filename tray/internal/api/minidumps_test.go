package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/minidump"
)

func TestPostMinidumps(t *testing.T) {
	var gotPath, gotAuth, gotType string
	var body struct {
		Dumps []map[string]any `json:"dumps"`
	}
	status := http.StatusOK
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotAuth, gotType = r.URL.Path, r.Header.Get("Authorization"), r.Header.Get("Content-Type")
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode: %v", err)
		}
		w.WriteHeader(status)
	}))
	defer srv.Close()

	c := api.New(srv.URL)
	c.SetAuth("dev-uid", "tok")
	dumps := []minidump.Summary{{FileName: "a.dmp", FileSize: 10, Supported: true, BugcheckCode: 0xD1}}
	if err := c.PostMinidumps(context.Background(), dumps); err != nil {
		t.Fatalf("PostMinidumps: %v", err)
	}
	if gotPath != "/api/tray/minidumps" || gotAuth != "Bearer tok" || gotType != "application/json" {
		t.Errorf("path %q auth %q type %q", gotPath, gotAuth, gotType)
	}
	if len(body.Dumps) != 1 || body.Dumps[0]["file_name"] != "a.dmp" || body.Dumps[0]["bugcheck_code"] != float64(0xD1) {
		t.Errorf("body = %+v", body.Dumps)
	}

	status = http.StatusInternalServerError
	if err := c.PostMinidumps(context.Background(), dumps); err == nil {
		t.Error("a 500 response must be an error")
	}
}
