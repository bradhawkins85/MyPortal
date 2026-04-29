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

.PHONY: all build-all build-windows build-darwin-amd64 build-darwin-arm64 test lint clean

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
	GOOS=windows GOARCH=amd64 CGO_ENABLED=0 GOFLAGS="-tags nowebview" \
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
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=0 GOFLAGS="-tags nowebview" \
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
	GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 GOFLAGS="-tags nowebview" \
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
