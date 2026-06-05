package main

import (
	"os"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestResolveEnvVarMenuLabelPrefersExplicitLabel(t *testing.T) {
	t.Setenv("COMPUTERNAME", "HOST-01")
	label := resolveEnvVarMenuLabel(api.MenuNode{
		Type:  "env_var",
		Name:  "COMPUTERNAME",
		Label: "Computer Name",
	})
	if label != "Computer Name" {
		t.Fatalf("expected explicit label, got %q", label)
	}
}

func TestResolveEnvVarMenuLabelUsesEnvValueByDefault(t *testing.T) {
	t.Setenv("COMPUTERNAME", "HOST-01")
	label := resolveEnvVarMenuLabel(api.MenuNode{
		Type: "env_var",
		Name: "COMPUTERNAME",
	})
	if label != "HOST-01" {
		t.Fatalf("expected env value label, got %q", label)
	}
}

func TestResolveEnvVarMenuLabelFallsBackToVarNameWhenUnset(t *testing.T) {
	const varName = "MYPORTAL_TEST_UNSET_COMPUTERNAME"
	_ = os.Unsetenv(varName)
	label := resolveEnvVarMenuLabel(api.MenuNode{
		Type: "env_var",
		Name: varName,
	})
	if label != varName {
		t.Fatalf("expected variable name fallback, got %q", label)
	}
}
