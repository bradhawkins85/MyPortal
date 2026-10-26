package main

import "testing"

func TestOSReleaseName(t *testing.T) {
	release := "PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"12\"\n"
	if got := osReleaseName(release); got != "Debian GNU/Linux 12 (bookworm)" {
		t.Fatalf("osReleaseName = %q", got)
	}
	if got := osReleaseName("NAME=Alpine\nVERSION_ID=3.20\n"); got != "Alpine 3.20" {
		t.Fatalf("osReleaseName without PRETTY_NAME = %q", got)
	}
}
