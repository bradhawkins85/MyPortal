package agent

import (
	"html"
	"regexp"
	"strings"
)

// clixmlErrorRe matches the error stream records in PowerShell's CLIXML
// stderr output (<S S="Error">...</S>).
var clixmlErrorRe = regexp.MustCompile(`(?s)<S S="Error">(.*?)</S>`)

// maxPowerShellErrorLen keeps an error short enough for a ticket note.
const maxPowerShellErrorLen = 600

// PowerShellErrorText turns PowerShell stderr into a readable message. When
// stderr is redirected PowerShell serialises it as CLIXML ("#< CLIXML"),
// which buries the actual error in XML and progress records; this extracts
// just the error lines.
func PowerShellErrorText(stderr string) string {
	text := strings.TrimSpace(stderr)
	if strings.HasPrefix(text, "#< CLIXML") {
		var lines []string
		for _, m := range clixmlErrorRe.FindAllStringSubmatch(text, -1) {
			line := html.UnescapeString(m[1])
			line = strings.NewReplacer("_x000D_", "", "_x000A_", "\n").Replace(line)
			for _, part := range strings.Split(line, "\n") {
				if part = strings.TrimSpace(part); part != "" {
					lines = append(lines, part)
				}
			}
		}
		text = strings.Join(lines, " ")
	}
	text = strings.Join(strings.Fields(text), " ")
	if len(text) > maxPowerShellErrorLen {
		text = text[:maxPowerShellErrorLen] + "…"
	}
	return text
}
