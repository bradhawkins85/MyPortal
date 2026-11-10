package api_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestRegisterDeviceKey(t *testing.T) {
	cases := map[int]error{
		http.StatusOK:               nil,
		http.StatusNotFound:         api.ErrDeviceKeyUnsupported,
		http.StatusMethodNotAllowed: api.ErrDeviceKeyUnsupported,
		http.StatusConflict:         api.ErrDeviceKeyConflict,
	}
	for status, want := range cases {
		var got struct {
			PublicKey string `json:"public_key"`
		}
		var auth string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/api/tray/device-key" || r.Method != http.MethodPost {
				t.Errorf("request %s %s", r.Method, r.URL.Path)
			}
			auth = r.Header.Get("Authorization")
			_ = json.NewDecoder(r.Body).Decode(&got)
			w.WriteHeader(status)
		}))
		client := api.New(srv.URL)
		client.SetAuth("uid", "device-token")
		err := client.RegisterDeviceKey(context.Background(), "cHVi")
		srv.Close()
		if !errors.Is(err, want) || (want == nil && err != nil) {
			t.Errorf("HTTP %d: err = %v, want %v", status, err, want)
		}
		if got.PublicKey != "cHVi" || auth != "Bearer device-token" {
			t.Errorf("HTTP %d: sent key %q with auth %q", status, got.PublicKey, auth)
		}
	}
}

func TestRegisterDeviceKeyReportsOtherFailures(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	err := api.New(srv.URL).RegisterDeviceKey(context.Background(), "cHVi")
	if err == nil || errors.Is(err, api.ErrDeviceKeyConflict) || errors.Is(err, api.ErrDeviceKeyUnsupported) {
		t.Fatalf("err = %v", err)
	}
}
