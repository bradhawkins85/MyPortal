package api

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
)

func portalWithSource(t *testing.T, sourceURL, field string) *httptest.Server {
	t.Helper()
	portal := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if sourceURL == "" {
			http.Error(w, "unavailable", http.StatusServiceUnavailable)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"source_url": sourceURL, "source_field": field})
	}))
	t.Cleanup(portal.Close)
	return portal
}

func TestQueryWANIPSourceUsesPublicRemoteAddrWhenFieldMissing(t *testing.T) {
	whoami := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("Hostname: whoami\nRemoteAddr: [2001:4860::8]:51234\n"))
	}))
	defer whoami.Close()
	ip, err := queryWANIPSource(context.Background(), whoami.Client(), whoami.URL, "X-Forwarded-For")
	if err != nil || ip.String() != "2001:4860::8" {
		t.Fatalf("queryWANIPSource = %v, %v; want 2001:4860::8", ip, err)
	}
}

func TestQueryWANIPSourceIgnoresPrivateRemoteAddr(t *testing.T) {
	whoami := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("RemoteAddr: 172.18.0.2:40000\n"))
	}))
	defer whoami.Close()
	if ip, err := queryWANIPSource(context.Background(), whoami.Client(), whoami.URL, "X-Forwarded-For"); err == nil {
		t.Fatalf("queryWANIPSource = %v; want an error for a proxy's private address", ip)
	}
}

func TestLookupWANAddressesQueriesEachFamily(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		if net.ParseIP(host).To4() != nil {
			_, _ = w.Write([]byte("X-Forwarded-For: 203.0.113.10\n"))
			return
		}
		_, _ = w.Write([]byte("X-Forwarded-For: 2001:db8::10\n"))
	})
	// A dual-stack listener answers both families on one URL.
	ln, err := net.Listen("tcp", "[::]:0")
	if err != nil {
		t.Skipf("dual-stack listener unavailable: %v", err)
	}
	whoami := &httptest.Server{Listener: ln, Config: &http.Server{Handler: handler}}
	whoami.Start()
	defer whoami.Close()
	port := ln.Addr().(*net.TCPAddr).Port
	if c, err := net.Dial("tcp6", net.JoinHostPort("::1", itoa(port))); err != nil {
		t.Skipf("IPv6 loopback unavailable: %v", err)
	} else {
		c.Close()
	}
	sourceURL := "http://localhost:" + itoa(port) + "/"
	defer func(old string) { wanFallbackURL = old }(wanFallbackURL)
	wanFallbackURL = "http://127.0.0.1:1/"

	client := New(portalWithSource(t, sourceURL, "X-Forwarded-For").URL)
	got, err := client.LookupWANAddresses(context.Background())
	if err != nil {
		t.Fatalf("LookupWANAddresses: %v", err)
	}
	if got.IPv4 != "203.0.113.10" || got.IPv6 != "2001:db8::10" {
		t.Fatalf("LookupWANAddresses = %+v", got)
	}
}

func TestLookupWANAddressesFallsBackToIcanhazip(t *testing.T) {
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("198.51.100.77\n"))
	}))
	defer fallback.Close()
	defer func(old string) { wanFallbackURL = old }(wanFallbackURL)
	wanFallbackURL = fallback.URL

	// The portal has no whoami source to offer (or is unreachable).
	client := New(portalWithSource(t, "", "").URL)
	got, err := client.LookupWANAddresses(context.Background())
	if err != nil {
		t.Fatalf("LookupWANAddresses: %v", err)
	}
	if got.IPv4 != "198.51.100.77" {
		t.Fatalf("LookupWANAddresses = %+v; want the fallback IPv4 address", got)
	}
}

func TestLookupWANAddressesFallsBackWhenWhoamiFails(t *testing.T) {
	whoami := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "down", http.StatusBadGateway)
	}))
	defer whoami.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("198.51.100.20\n"))
	}))
	defer fallback.Close()
	defer func(old string) { wanFallbackURL = old }(wanFallbackURL)
	wanFallbackURL = fallback.URL

	client := New(portalWithSource(t, whoami.URL, "X-Forwarded-For").URL)
	got, err := client.LookupWANAddresses(context.Background())
	if err != nil || got.IPv4 != "198.51.100.20" {
		t.Fatalf("LookupWANAddresses = %+v, %v; want IPv4 from fallback", got, err)
	}
}

func itoa(n int) string { return strconv.Itoa(n) }

func TestLookupWANAddressesReturnsCloudflareGeo(t *testing.T) {
	whoami := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("Hostname: whoami\nCf-Ipcity: Brisbane\nCf-Ipcountry: AU\n" +
			"Cf-Region-Code: QLD\nX-Forwarded-For: 203.0.113.30, 172.18.0.1\n"))
	}))
	defer whoami.Close()
	defer func(old string) { wanFallbackURL = old }(wanFallbackURL)
	wanFallbackURL = "http://127.0.0.1:1/"

	client := New(portalWithSource(t, whoami.URL, "X-Forwarded-For").URL)
	got, err := client.LookupWANAddresses(context.Background())
	if err != nil {
		t.Fatalf("LookupWANAddresses: %v", err)
	}
	want := WANGeo{City: "Brisbane", Country: "AU", RegionCode: "QLD"}
	if got.IPv4 != "203.0.113.30" || got.Geo != want {
		t.Fatalf("LookupWANAddresses = %+v; want IPv4 203.0.113.30 and %+v", got, want)
	}
}

func TestFallbackLookupHasNoGeo(t *testing.T) {
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("198.51.100.77\n"))
	}))
	defer fallback.Close()
	defer func(old string) { wanFallbackURL = old }(wanFallbackURL)
	wanFallbackURL = fallback.URL

	client := New(portalWithSource(t, "", "").URL)
	got, err := client.LookupWANAddresses(context.Background())
	if err != nil || !got.Geo.empty() {
		t.Fatalf("LookupWANAddresses = %+v, %v; want no location from icanhazip", got, err)
	}
}
