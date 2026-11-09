package api

import (
	"net/http"
	"net/url"
	"testing"
)

func TestSameOriginHTTPSRedirect(t *testing.T) {
	req := func(raw string) *http.Request {
		u, err := url.Parse(raw)
		if err != nil {
			t.Fatal(err)
		}
		return &http.Request{URL: u}
	}
	cases := []struct {
		from, to string
		follow   bool
	}{
		{"https://portal.example.com/api/x", "https://portal.example.com/api/y", true},
		{"http://portal.example.com/api/x", "https://portal.example.com/api/x", true},
		{"https://portal.example.com/api/x", "http://portal.example.com/api/x", false},
		{"https://portal.example.com/api/x", "https://portal.example.com:8443/api/x", false},
		{"https://portal.example.com/api/x", "https://evil.example.com/api/x", false},
		{"https://portal.example.com/api/x", "https://sub.portal.example.com/api/x", false},
	}
	for _, c := range cases {
		err := sameOriginHTTPSRedirect(req(c.to), []*http.Request{req(c.from)})
		if got := err == nil; got != c.follow {
			t.Errorf("%s -> %s: follow=%v, want %v", c.from, c.to, got, c.follow)
		}
	}
	via := make([]*http.Request, 5)
	for i := range via {
		via[i] = req("https://portal.example.com/")
	}
	if sameOriginHTTPSRedirect(req("https://portal.example.com/"), via) == nil {
		t.Error("followed a sixth redirect")
	}
}
