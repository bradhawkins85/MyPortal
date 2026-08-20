package main

import (
	"os"
	"os/user"
	"runtime"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func resolveEnvVarValue(name string) string {
	name = normalizeEnvVarName(name)
	if value := os.Getenv(name); value != "" {
		return value
	}
	if runtime.GOOS == "darwin" {
		switch strings.ToUpper(name) {
		case "COMPUTERNAME", "HOSTNAME":
			if hostname, err := os.Hostname(); err == nil {
				return hostname
			}
		case "USERNAME":
			if value := os.Getenv("USER"); value != "" {
				return value
			}
			if current, err := user.Current(); err == nil {
				return current.Username
			}
		case "USERPROFILE":
			if home, err := os.UserHomeDir(); err == nil {
				return home
			}
		}
	}
	return ""
}

func normalizeEnvVarName(name string) string {
	return strings.TrimSpace(name)
}

func resolveEnvVarMenuLabel(node api.MenuNode) string {
	if node.Label != "" {
		return node.Label
	}
	varName := normalizeEnvVarName(node.Name)
	if varName == "" {
		return ""
	}
	if val := resolveEnvVarValue(varName); val != "" {
		return val
	}
	return varName
}
