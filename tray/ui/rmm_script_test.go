package main

import (
	"net/http"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestRMMScriptLabelPrefersMenuLabelThenScriptName(t *testing.T) {
	cases := []struct {
		node api.MenuNode
		want string
	}{
		{api.MenuNode{Label: "Clear Teams cache", ScriptName: "Clear-TeamsCache", ScriptID: 4}, "Clear Teams cache"},
		{api.MenuNode{Label: "  ", ScriptName: "Clear-TeamsCache", ScriptID: 4}, "Clear-TeamsCache"},
		{api.MenuNode{ScriptID: 4}, "Script #4"},
	}
	for _, tc := range cases {
		if got := rmmScriptLabel(tc.node); got != tc.want {
			t.Errorf("rmmScriptLabel(%+v) = %q, want %q", tc.node, got, tc.want)
		}
	}
}

func TestRMMScriptErrorMessageShowsPortalExplanation(t *testing.T) {
	body := []byte(`{"detail":"The MyPortal RMM agent is not installed on this computer."}`)
	if got := rmmScriptErrorMessage(http.StatusConflict, body); got != "The MyPortal RMM agent is not installed on this computer." {
		t.Fatalf("unexpected message: %q", got)
	}
}

func TestRMMScriptErrorMessageHidesOtherFailures(t *testing.T) {
	want := "MyPortal could not start the script. Please contact support."
	for _, tc := range []struct {
		status int
		body   string
	}{
		{http.StatusInternalServerError, `{"detail":"Traceback ..."}`},
		{http.StatusConflict, `not json`},
		{http.StatusUnprocessableEntity, `{"detail":[{"msg":"bad"}]}`},
	} {
		if got := rmmScriptErrorMessage(tc.status, []byte(tc.body)); got != want {
			t.Errorf("status %d: got %q, want %q", tc.status, got, want)
		}
	}
}

func TestMenuNodeTypeRecognizesRMMScript(t *testing.T) {
	if got := normalizedMenuNodeType(" RMM_Script "); got != "rmm_script" {
		t.Fatalf("normalizedMenuNodeType = %q", got)
	}
}
