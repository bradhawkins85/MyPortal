package agent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestChatEmptyConfig(t *testing.T) {
	c := newLLMClient()
	if _, err := c.Chat(context.Background(), LLMConfig{BaseURL: "", Model: "m"}, "s", "u"); err == nil {
		t.Error("expected error for empty base URL")
	}
	if _, err := c.Chat(context.Background(), LLMConfig{BaseURL: "http://x", Model: ""}, "s", "u"); err == nil {
		t.Error("expected error for empty model")
	}
}

func TestChatSendsAndParses(t *testing.T) {
	var gotAuth, gotModel string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			t.Errorf("unexpected path %q", r.URL.Path)
		}
		gotAuth = r.Header.Get("Authorization")
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		gotModel, _ = body["model"].(string)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"role":"assistant","content":"restart it"}}]}`))
	}))
	defer srv.Close()

	c := newLLMClient()
	out, err := c.Chat(context.Background(), LLMConfig{BaseURL: srv.URL, Model: "llama3", APIKey: "sekret"}, "system", "user")
	if err != nil {
		t.Fatalf("Chat: %v", err)
	}
	if out != "restart it" {
		t.Errorf("Chat = %q, want %q", out, "restart it")
	}
	if gotAuth != "Bearer sekret" {
		t.Errorf("Authorization = %q, want %q", gotAuth, "Bearer sekret")
	}
	if gotModel != "llama3" {
		t.Errorf("model = %q, want llama3", gotModel)
	}
}

func TestChatServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(`{"detail":"upstream down"}`))
	}))
	defer srv.Close()

	c := newLLMClient()
	if _, err := c.Chat(context.Background(), LLMConfig{BaseURL: srv.URL, Model: "m"}, "s", "u"); err == nil {
		t.Fatal("expected an error on HTTP 502")
	}
}

func TestChatNoChoices(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[]}`))
	}))
	defer srv.Close()

	c := newLLMClient()
	_, err := c.Chat(context.Background(), LLMConfig{BaseURL: srv.URL, Model: "m"}, "s", "u")
	if err == nil || !strings.Contains(err.Error(), "no choices") {
		t.Errorf("expected no-choices error, got %v", err)
	}
}
