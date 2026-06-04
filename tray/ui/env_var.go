package main

import (
	"os"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func resolveEnvVarName(name string) string {
	return strings.TrimSpace(name)
}

func resolveEnvVarMenuLabel(node api.MenuNode) string {
	if node.Label != "" {
		return node.Label
	}
	varName := resolveEnvVarName(node.Name)
	if varName == "" {
		return ""
	}
	if val := os.Getenv(varName); val != "" {
		return val
	}
	return varName
}
