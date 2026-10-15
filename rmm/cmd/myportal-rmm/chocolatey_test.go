package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func TestRefreshChocolateyPlanUsesLatestCompanyPolicy(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/api/rmm/agent/chocolatey/sources" || r.Header.Get("Authorization") != "Bearer agent-token" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"script":"sync", "sources":[{"name":"nexus", "enabled":true}]}`))
	}))
	defer server.Close()
	api := client.New(server.URL, "agent-token")
	job := client.Job{Env: map[string]string{"MYPORTAL_PATCH_PLAN": `{"apps":[{"package_id":"app"}],"sources":[{"name":"stale"}],"reboot":{"reboot_mode":"never"}}`}}
	if !refreshChocolateyPlan(context.Background(), api, &job) {
		t.Fatal("source refresh failed")
	}
	var plan struct {
		Sources []struct{ Name string }
		Apps    []struct {
			PackageID string `json:"package_id"`
		}
	}
	if err := json.Unmarshal([]byte(job.Env["MYPORTAL_PATCH_PLAN"]), &plan); err != nil {
		t.Fatal(err)
	}
	if len(plan.Sources) != 1 || plan.Sources[0].Name != "nexus" || plan.Apps[0].PackageID != "app" || calls != 1 {
		t.Fatal("job did not retain its application and refresh sources")
	}
	windows := client.Job{Env: map[string]string{"MYPORTAL_PATCH_PLAN": `{"apps":[]}`}}
	if !refreshChocolateyPlan(context.Background(), api, &windows) || calls != 1 {
		t.Fatal("Windows-only jobs should not need Chocolatey sources")
	}
}

func TestRefreshChocolateyPlanStopsOnSourceFailure(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()
	job := client.Job{Env: map[string]string{"MYPORTAL_PATCH_PLAN": `{"apps":[{"package_id":"app"}]}`}}
	if refreshChocolateyPlan(context.Background(), client.New(server.URL, "token"), &job) {
		t.Fatal("application job must stop when company sources are unavailable")
	}
}
