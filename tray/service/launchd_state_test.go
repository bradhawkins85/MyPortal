package main

import "testing"

func TestParseLaunchdJobState(t *testing.T) {
	cases := []struct {
		name string
		out  string
		want launchdJobState
	}{
		{
			name: "running",
			out: `gui/501/io.myportal.tray.agent = {
	active count = 1
	path = /Library/LaunchAgents/io.myportal.tray.agent.plist
	state = running
	pid = 812
	last exit code = (never exited)
}`,
			want: launchdJobState{Running: true, NeverExited: true},
		},
		{
			name: "loaded but never started",
			out: `gui/501/io.myportal.tray.agent = {
	active count = 0
	state = not running
	last exit code = (never exited)
}`,
			want: launchdJobState{NeverExited: true},
		},
		{
			name: "quit by the user",
			out: `gui/501/io.myportal.tray.agent = {
	state = not running
	last exit code = 0
}`,
			want: launchdJobState{},
		},
		{
			name: "empty",
			out:  "",
			want: launchdJobState{},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := parseLaunchdJobState(tc.out); got != tc.want {
				t.Fatalf("parseLaunchdJobState = %+v, want %+v", got, tc.want)
			}
		})
	}
}
