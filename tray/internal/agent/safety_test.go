package agent

import (
	"strings"
	"testing"
)

func TestScrubRedactsSecrets(t *testing.T) {
	cases := []struct {
		name    string
		input   string
		wantSub string
		notSub  string
	}{
		{"bearer token", "Authorization: Bearer abc123.def-456", "[REDACTED]", "abc123.def-456"},
		{"authorization header", "authorization=Basic dXNlcjpwYXNz", "[REDACTED]", "Basic dXNlcjpwYXNz"},
		{"aws access key", "key AKIAIOSFODNN7EXAMPLE used", "[REDACTED_AWS_KEY]", "AKIAIOSFODNN7EXAMPLE"},
		{"password assignment", "service password: hunter2", "[REDACTED]", "hunter2"},
		{"api key quoted", "apiKey = \"sk-live-999\"", "[REDACTED]", "sk-live-999"},
		{"email", "contact: jdoe@corp.example.com ok", "[REDACTED_EMAIL]", "jdoe@corp.example.com"},
		{"ipv4", "conn from 10.20.30.40 dropped", "[REDACTED_IP]", "10.20.30.40"},
		{"private key block", "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", "abc\n-----END"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := Scrub(c.input)
			if !strings.Contains(got, c.wantSub) {
				t.Errorf("Scrub(%q) = %q, want it to contain %q", c.input, got, c.wantSub)
			}
			if c.notSub != "" && strings.Contains(got, c.notSub) {
				t.Errorf("Scrub(%q) = %q, still contains %q", c.input, got, c.notSub)
			}
		})
	}
}

func TestScrubEmpty(t *testing.T) {
	if got := Scrub(""); got != "" {
		t.Errorf("Scrub(\"\") = %q, want empty", got)
	}
}

func TestScrubPreservesNormalText(t *testing.T) {
	in := "2024-05-01 10:00:00 [Info] service started, pid=1234"
	got := Scrub(in)
	if !strings.Contains(got, "service started") {
		t.Errorf("Scrub mangled benign log line: %q", got)
	}
}

func TestTruncate(t *testing.T) {
	t.Run("short passthrough", func(t *testing.T) {
		got, truncated := Truncate("hello", 10)
		if got != "hello" || truncated {
			t.Errorf("Truncate(short) = %q, %v; want %q, false", got, truncated, "hello")
		}
	})
	t.Run("truncates long", func(t *testing.T) {
		got, truncated := Truncate(strings.Repeat("a", 100), 10)
		if !truncated {
			t.Error("expected truncation flag to be true")
		}
		if len([]byte(got)) > 10+len("\n…[truncated]") {
			t.Errorf("Truncated length %d exceeds budget", len([]byte(got)))
		}
	})
	t.Run("multi-byte boundary", func(t *testing.T) {
		// "é" is two bytes in UTF-8; make sure we never split it.
		in := "é"
		for len(in) < 20 {
			in += "é"
		}
		got, truncated := Truncate(in, 7)
		if !truncated {
			t.Fatal("expected truncation")
		}
		if !strings.HasSuffix(got, "…[truncated]") {
			t.Errorf("expected truncated marker, got %q", got)
		}
	})
	t.Run("non-positive max", func(t *testing.T) {
		got, truncated := Truncate("anything", 0)
		if got != "anything" || truncated {
			t.Errorf("Truncate max=0 = %q, %v; want unchanged, false", got, truncated)
		}
	})
}
