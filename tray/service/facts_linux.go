package main

import (
	"os"
	"strings"
)

// platformFacts reads the distribution (for example "Debian GNU/Linux 12
// (bookworm)", which Proxmox VE reports) and the DMI serial number, so the
// server can name and match the device's asset.
func platformFacts(f *facts) {
	f.OSVersion = osReleaseName(readTrimmed("/etc/os-release"))
	f.SerialNumber = readTrimmed("/sys/class/dmi/id/product_serial")
}

func readTrimmed(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func osReleaseName(release string) string {
	values := map[string]string{}
	for _, line := range strings.Split(release, "\n") {
		key, value, ok := strings.Cut(strings.TrimSpace(line), "=")
		if ok {
			values[key] = strings.Trim(value, `"'`)
		}
	}
	name := values["PRETTY_NAME"]
	if name == "" {
		name = strings.TrimSpace(values["NAME"] + " " + values["VERSION_ID"])
	}
	if len(name) > 64 {
		name = name[:64]
	}
	return name
}
