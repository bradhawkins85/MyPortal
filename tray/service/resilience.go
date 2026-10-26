package main

import "time"

// Recovery settings for the Windows service. Windows repeats the last action
// for every failure after the third, so the service is always restarted.
var serviceRestartDelays = []time.Duration{5 * time.Second, 15 * time.Second, 60 * time.Second}

// serviceFailureResetPeriod is how long, in seconds, the service must run
// without failing before Windows resets its failure count.
const serviceFailureResetPeriod = 24 * 60 * 60

// safeBootKeys are the registry keys, under HKLM, that let Windows start the
// named service in Safe Mode and Safe Mode with Networking.
func safeBootKeys(name string) []string {
	const root = `SYSTEM\CurrentControlSet\Control\SafeBoot\`
	return []string{root + `Minimal\` + name, root + `Network\` + name}
}
