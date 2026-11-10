package main

import "testing"

func TestPickConsoleUser(t *testing.T) {
	cases := []struct {
		name     string
		sessions []consoleSession
		console  uint32
		want     string
	}{
		{"nobody signed in", nil, 1, ""},
		{"active console user wins over disconnected user", []consoleSession{
			{ID: 1, Active: false, User: `CORP\alice`},
			{ID: 2, Active: true, User: `CORP\bob`},
		}, 2, `CORP\bob`},
		{"disconnected users only", []consoleSession{
			{ID: 1, Active: false, User: `CORP\alice`},
			{ID: 3, Active: false, User: `CORP\carol`},
		}, 4, ""},
		{"console at sign-in screen falls back to active remote session", []consoleSession{
			{ID: 1, Active: true, User: ""},
			{ID: 5, Active: true, User: `CORP\dave`},
			{ID: 3, Active: true, User: `CORP\carol`},
			{ID: 2, Active: false, User: `CORP\alice`},
		}, 1, `CORP\carol`},
		{"active console beats lower-numbered remote session", []consoleSession{
			{ID: 2, Active: true, User: `CORP\erin`},
			{ID: 4, Active: true, User: `CORP\frank`},
		}, 4, `CORP\frank`},
	}
	for _, tc := range cases {
		if got := pickConsoleUser(tc.sessions, tc.console); got != tc.want {
			t.Errorf("%s: got %q, want %q", tc.name, got, tc.want)
		}
	}
}
