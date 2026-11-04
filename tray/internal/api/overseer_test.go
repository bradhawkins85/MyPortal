package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestReportOverseer(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tray/overseer/report" || r.Method != http.MethodPost {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer tok" {
			t.Errorf("Authorization = %q", got)
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["actions"] == nil || body["services"] == nil {
			t.Errorf("lists must be sent as [] not null: %v", body)
		}
		w.Write([]byte(`{"tray":{"enrolled":true,"last_seen_age_seconds":12},"rmm":{"enrolled":false,"last_seen_age_seconds":null},"check_interval_seconds":300}`))
	}))
	defer srv.Close()
	c := New(srv.URL)
	c.SetAuth("dev", "tok")
	status, err := c.ReportOverseer(context.Background(), OverseerReport{OverseerVersion: "1.0"})
	if err != nil {
		t.Fatal(err)
	}
	if !status.Tray.Enrolled || status.Tray.LastSeenAgeSeconds == nil || *status.Tray.LastSeenAgeSeconds != 12 {
		t.Errorf("tray = %+v", status.Tray)
	}
	if status.RMM.Enrolled || status.RMM.LastSeenAgeSeconds != nil || status.CheckIntervalSeconds != 300 {
		t.Errorf("status = %+v", status)
	}
}

func TestReportOverseerHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()
	_, err := New(srv.URL).ReportOverseer(context.Background(), OverseerReport{})
	var httpErr *HTTPStatusError
	if !errors.As(err, &httpErr) || httpErr.Status != http.StatusUnauthorized {
		t.Fatalf("err = %v", err)
	}
}
