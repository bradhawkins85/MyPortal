package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestManualCheckinRunsRemainingStepsAfterFailure(t *testing.T) {
	finished := false
	result := runCheckinSteps(context.Background(), []checkinStep{
		{"first", func(context.Context) error { return errors.New("upload unavailable") }},
		{"second", func(context.Context) error { finished = true; return nil }},
	})
	if !finished || result.ExitCode == nil || *result.ExitCode != 1 {
		t.Fatalf("incomplete result: %+v", result)
	}
	if !strings.Contains(result.Stderr, "first: upload unavailable") || !strings.Contains(result.Stdout, "second: complete") {
		t.Fatalf("missing per-step outcome: %+v", result)
	}
}

func TestManualCheckinRespectsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := runCheckinSteps(ctx, []checkinStep{{"inventory", func(context.Context) error { t.Fatal("cancelled task ran"); return nil }}})
	if result.ExitCode == nil || *result.ExitCode == 0 || !strings.Contains(result.Stderr, "context canceled") {
		t.Fatalf("unexpected result: %+v", result)
	}
}
