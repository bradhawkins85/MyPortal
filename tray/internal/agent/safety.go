package agent

import (
	"regexp"
	"unicode/utf8"
)

// The redaction rules below are intentionally conservative: over-redacting a
// log line is far safer than leaking a credential into a ticket note. Each
// rule replaces the matched secret with a short, non-reversible placeholder.
//
// Rules run in order. Order matters only where patterns overlap (for example
// the Authorization-header rule runs before the generic key=value rule so a
// full "Authorization: Bearer xyz" line is handled as a unit).
var secretPatterns = []struct {
	name string
	re   *regexp.Regexp
	repl string
}{
	{
		name: "private-key",
		re:   regexp.MustCompile(`(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----`),
		repl: "[REDACTED_PRIVATE_KEY]",
	},
	{
		name: "authorization-header",
		// Redact the entire rest of the line so both "Bearer xyz" and
		// "Basic xyz" (and any trailing comment) are removed as a unit.
		re:   regexp.MustCompile(`(?i)\b(authorization)\s*[:=]\s*\S+[^\r\n]*`),
		repl: "Authorization: [REDACTED]",
	},
	{
		name: "bearer",
		re:   regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/-]+`),
		repl: "Bearer [REDACTED_TOKEN]",
	},
	{
		name: "aws-access-key",
		re:   regexp.MustCompile(`\b(A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b`),
		repl: "[REDACTED_AWS_KEY]",
	},
	{
		name: "key-value-secret",
		// Keep the field name and delimiter; drop the value. Group 1 is the
		// key, group 2 is the ":" or "=" delimiter.
		re:   regexp.MustCompile(`(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret|private[_-]?key)\s*(:|=)\s*("[^"]*"|'[^']*'|\S+)`),
		repl: "${1} ${2} [REDACTED]",
	},
	{
		name: "email",
		re:   regexp.MustCompile(`\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`),
		repl: "[REDACTED_EMAIL]",
	},
	{
		name: "ipv4",
		re:   regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`),
		repl: "[REDACTED_IP]",
	},
}

// Scrub returns s with anything that looks like a secret or personal data
// replaced by a non-reversible placeholder.
func Scrub(s string) string {
	if s == "" {
		return s
	}
	for _, p := range secretPatterns {
		s = p.re.ReplaceAllString(s, p.repl)
	}
	return s
}

// Truncate shortens s to at most max bytes on a rune boundary, keeping the
// head of the text and appending a marker if anything was dropped. It reports
// whether truncation occurred.
func Truncate(s string, max int) (string, bool) {
	if max <= 0 || len(s) <= max {
		return s, false
	}
	end := max
	for end > 0 && !utf8.RuneStart(s[end]) {
		end--
	}
	return s[:end] + "\n…[truncated]", true
}
