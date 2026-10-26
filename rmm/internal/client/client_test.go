package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestEnrolSendsTrayTokenAndUID(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/rmm/agent/enrol" || r.Header.Get("Authorization") != "Bearer tray-token" {
			http.Error(w, "bad", http.StatusBadRequest)
			return
		}
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["agent_uid"] != "uid-12345678" || body["hostname"] != "pc1" {
			http.Error(w, "bad body", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"agent_uid": "uid-12345678", "auth_token": "agent-token"})
	}))
	defer server.Close()
	result, err := Enrol(context.Background(), server.URL+"/", "tray-token", "uid-12345678", Details{Hostname: "pc1"}) // gitleaks:allow (test value)
	if err != nil || result.AuthToken != "agent-token" {
		t.Fatalf("Enrol = %#v, %v", result, err)
	}
}

func TestJobsAndReport(t *testing.T) {
	var reported Result
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer agent-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/api/rmm/agent/jobs":
			if r.URL.Query().Get("wait") != "25" || r.URL.Query().Get("executing") != "3,4" {
				http.Error(w, "wait", http.StatusBadRequest)
				return
			}
			_, _ = w.Write([]byte(`{"jobs":[{"id":5,"language":"bash","script":"echo","run_anyway":true,"parameters":{"A":1}}],"stop":[4]}`))
		case "/api/rmm/agent/runs/5/result":
			_ = json.NewDecoder(r.Body).Decode(&reported)
			_, _ = w.Write([]byte(`{"status":"completed"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	api := New(server.URL, "agent-token")
	poll, err := api.Jobs(context.Background(), 25*time.Second, []int64{3, 4})
	if err != nil || len(poll.Jobs) != 1 || poll.Jobs[0].ID != 5 || !poll.Jobs[0].RunAnyway {
		t.Fatalf("Jobs = %#v, %v", poll, err)
	}
	if len(poll.Stop) != 1 || poll.Stop[0] != 4 {
		t.Fatalf("Stop = %#v", poll.Stop)
	}
	code := 0
	if err := api.Report(context.Background(), 5, Result{ExitCode: &code, Stdout: "ok"}); err != nil {
		t.Fatal(err)
	}
	if reported.Stdout != "ok" || reported.CustomValues == nil {
		t.Fatalf("reported = %#v", reported)
	}
	if _, err := New(server.URL, "wrong").Jobs(context.Background(), 25*time.Second, nil); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("expected ErrUnauthorized, got %v", err)
	}
}
