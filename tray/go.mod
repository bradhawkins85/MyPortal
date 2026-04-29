module github.com/bradhawkins85/myportal-tray

go 1.22

require (
	github.com/getlantern/systray v1.2.2
	github.com/gorilla/websocket v1.5.3
	github.com/kardianos/service v1.2.2
	github.com/webview/webview_go v0.0.0-20240831120633-6173450d4dd6
	golang.org/x/sys v0.1.0
)

require (
	github.com/getlantern/context v0.0.0-20190109183933-c447772a6520 // indirect
	github.com/getlantern/errors v0.0.0-20190325191628-abdb3e3e36f7 // indirect
	github.com/getlantern/golog v0.0.0-20190830074920-4ef2e798c2d7 // indirect
	github.com/getlantern/hex v0.0.0-20190417191902-c6586a6fe0b7 // indirect
	github.com/getlantern/hidden v0.0.0-20190325191715-f02dbb02be55 // indirect
	github.com/getlantern/ops v0.0.0-20190325191751-d70cb0d6f85f // indirect
	github.com/go-stack/stack v1.8.0 // indirect
	github.com/oxtoacart/bpool v0.0.0-20190530202638-03653db5a59c // indirect
)

// systray and webview are guarded by !nowebview build tags so they are
// NOT pulled in for the standard CGO=0 cross-compile. They are listed
// here as optional; add them manually when building native UI:
//   go get github.com/getlantern/systray@latest
//   go get github.com/webview/webview_go@latest
