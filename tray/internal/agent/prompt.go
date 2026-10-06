package agent

import (
	"fmt"
	"runtime"
	"strings"
)

// SystemPrompt anchors the model as a careful IT-troubleshooting assistant and
// forbids it from inventing details or asking for more data (it cannot).
const SystemPrompt = `You are a careful senior IT troubleshooting assistant for a managed-service provider.
You are given a description of a reported problem and a bounded, already-redacted
sample of recent endpoint logs. Provide concise, practical remediation guidance:

- Start with the most likely root cause(s), most probable first.
- Then list concrete, ordered troubleshooting steps a technician can perform.
- Note which log entries or events support each hypothesis.
- If the logs are insufficient, say exactly what additional information would help.

Rules:
- Never invent log entries, timestamps, or details that are not present.
- Do not repeat any redacted secret; treat [REDACTED_...] placeholders as absent.
- Keep the answer focused and under ~600 words.
- Respond in plain text (light markdown is fine); do not call tools or ask questions.`

// buildUserPrompt assembles the user turn from the request and the log sample.
func buildUserPrompt(req Request, sources []string, logSample string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Endpoint: %s\n", req.Endpoint)
	fmt.Fprintf(&b, "Operating system: %s\n", runtime.GOOS)
	fmt.Fprintf(&b, "Reported problem: %s\n", orNote(req.Prompt))
	if len(sources) > 0 {
		fmt.Fprintf(&b, "Log sources: %s\n", strings.Join(sources, ", "))
	}
	b.WriteString("\nRecent endpoint logs (redacted):\n")
	if strings.TrimSpace(logSample) == "" {
		b.WriteString("[no logs available]\n")
	} else {
		b.WriteString(logSample)
		b.WriteString("\n")
	}
	b.WriteString("\nBased on the above, what is the most likely cause and what should we do next?")
	return b.String()
}

func orNote(s string) string {
	if strings.TrimSpace(s) == "" {
		return "(not provided)"
	}
	return s
}
