## MyPortal Tray App — Makefile
## Cross-compiles the service and UI agent for Windows (amd64) and macOS
## (amd64 + arm64) from a Linux CI host.
##
## Prerequisites (Linux CI):
##   - Go 1.22+
##   - For macOS builds: zig (used as cross-compiler via GOFLAGS)
##   - For Windows builds: MinGW-w64 (for CGO from webview_go)
##     OR set CGO_ENABLED=0 and build ui without webview (headless mode)
##
## Targets:
##   make build-all         Build all platform targets
##   make build-windows     Build Windows (amd64)
##   make build-darwin-amd64
##   make build-darwin-arm64
##   make test              Run Go tests
##   make clean

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "0.0.0-dev")
LDFLAGS  := -X 'github.com/bradhawkins85/myportal-tray/internal/updater.AgentVersion=$(VERSION)'
DIST     := dist

.PHONY: all build-all build-windows build-darwin-amd64 build-darwin-arm64 \
        build-darwin-universal build-msi build-pkg package-all test lint clean

all: build-all

build-all: build-windows build-darwin-amd64 build-darwin-arm64

# -----------------------------------------------------------------------
# Windows amd64
# -----------------------------------------------------------------------
build-windows:
	@mkdir -p $(DIST)/windows
	@echo "Building Windows amd64 service..."
	GOOS=windows GOARCH=amd64 CGO_ENABLED=0 \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/windows/myportal-tray-service.exe ./service
	@echo "Building Windows amd64 UI (no-CGO, no webview)..."
	GOOS=windows GOARCH=amd64 CGO_ENABLED=0 GOFLAGS="-tags=nowebview" \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/windows/myportal-tray-ui.exe ./ui

# -----------------------------------------------------------------------
# macOS amd64 (Intel)
# -----------------------------------------------------------------------
build-darwin-amd64:
	@mkdir -p $(DIST)/darwin-amd64
	@echo "Building macOS amd64 service..."
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=0 \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/darwin-amd64/myportal-tray-service ./service
	@echo "Building macOS amd64 UI (no-CGO, no webview)..."
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=0 GOFLAGS="-tags=nowebview" \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/darwin-amd64/myportal-tray-ui ./ui

# -----------------------------------------------------------------------
# macOS arm64 (Apple Silicon)
# -----------------------------------------------------------------------
build-darwin-arm64:
	@mkdir -p $(DIST)/darwin-arm64
	@echo "Building macOS arm64 service..."
	GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/darwin-arm64/myportal-tray-service ./service
	@echo "Building macOS arm64 UI (no-CGO, no webview)..."
	GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 GOFLAGS="-tags=nowebview" \
	  go build -ldflags "$(LDFLAGS)" -o $(DIST)/darwin-arm64/myportal-tray-ui ./ui

# -----------------------------------------------------------------------
# Universal macOS binary (lipo from amd64 + arm64)
# -----------------------------------------------------------------------
build-darwin-universal: build-darwin-amd64 build-darwin-arm64
	@mkdir -p $(DIST)/darwin
	lipo -create -output $(DIST)/darwin/myportal-tray-service \
	    $(DIST)/darwin-amd64/myportal-tray-service \
	    $(DIST)/darwin-arm64/myportal-tray-service
	lipo -create -output $(DIST)/darwin/myportal-tray-ui \
	    $(DIST)/darwin-amd64/myportal-tray-ui \
	    $(DIST)/darwin-arm64/myportal-tray-ui

# -----------------------------------------------------------------------
# Windows MSI installer
# Requires WiX v7: dotnet tool install --global wix --version "7.*"
#
# WiX v7 requires accepting the FireGiant Open Source Maintenance Fee (OSMF)
# EULA. We pass `-acceptEula wix7` on the command line per the official
# instructions (https://docs.firegiant.com/wix/osmf/) so unattended builds
# do not fail with WIX7015.
#
# IMPORTANT: WiX is Windows-only despite shipping as a .NET global tool.
# On Linux/macOS the Directory/@Name validator always fails (WIX0389) because
# it depends on Windows path semantics — see wixtoolset/issues#7154.
# The MSI must therefore be built on a Windows host (or via a Windows CI
# runner). On non-Windows hosts this target prints an explanatory message
# and exits successfully so callers (e.g. scripts/upgrade.sh) don't fail.
# -----------------------------------------------------------------------
HOST_OS := $(shell uname -s 2>/dev/null || echo Unknown)

build-msi: build-windows
	@case "$(HOST_OS)" in \
	    MINGW*|MSYS*|CYGWIN*|Windows_NT) \
	        echo "Building Windows MSI installer..."; \
	        wix build -acceptEula wix7 installer/windows/myportal-tray.wxs \
	            -d BinDir=$(CURDIR)/$(DIST)/windows \
	            -o $(DIST)/windows/myportal-tray.msi ;; \
	    *) \
	        echo "Skipping MSI build: WiX only supports Windows hosts (host: $(HOST_OS))."; \
	        echo "Run 'make build-msi' on a Windows machine, or build via the GitHub Actions windows-latest runner."; \
	        exit 0 ;; \
	esac

# -----------------------------------------------------------------------
# macOS .pkg installer
# Requires pkgbuild / productbuild — macOS only.
# Depends on the universal binary; run build-darwin-universal first if
# you want a fat binary, otherwise run build-darwin-amd64 / arm64.
# -----------------------------------------------------------------------
build-pkg: build-darwin-universal
	@echo "Building macOS .pkg installer..."
	(cd installer/macos && bash build-pkg.sh "$(VERSION)")
	mv installer/macos/myportal-tray-$(VERSION).pkg $(DIST)/darwin/myportal-tray.pkg
	rm -f installer/macos/myportal-tray-$(VERSION)-unsigned.pkg

# -----------------------------------------------------------------------
# Package all installers
# Builds the MSI on Windows hosts that have WiX v7.
# Builds the macOS .pkg only when pkgbuild is present (macOS runners).
# -----------------------------------------------------------------------
package-all: build-msi
	@if command -v pkgbuild >/dev/null 2>&1; then \
	    $(MAKE) build-pkg; \
	else \
	    echo "pkgbuild not available; skipping macOS .pkg (macOS-only step)."; \
	fi

# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------
test:
	go test -count=1 ./...

# -----------------------------------------------------------------------
# Lint (requires golangci-lint)
# -----------------------------------------------------------------------
lint:
	golangci-lint run ./...

# -----------------------------------------------------------------------
# Clean
# -----------------------------------------------------------------------
clean:
	rm -rf $(DIST)
