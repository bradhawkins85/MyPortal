package main

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func TestRunAnywayStartsWithAllOrdinarySlotsOccupied(t *testing.T) {
	slots := make(chan struct{}, maxConcurrent)
	for i := 0; i < maxConcurrent; i++ {
		slots <- struct{}{}
	}
	var wg sync.WaitGroup
	started := make(chan struct{})
	if !startJob(context.Background(), slots, &wg, client.Job{RunAnyway: true}, func(client.Job) { close(started) }) {
		t.Fatal("Run Anyway job was rejected")
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("Run Anyway waited for an ordinary execution slot")
	}
	wg.Wait()
	if len(slots) != maxConcurrent {
		t.Fatal("Run Anyway consumed or released an ordinary slot")
	}
}

func TestOrdinaryJobWaitsForSlotAndCancellationStopsWaiting(t *testing.T) {
	slots := make(chan struct{}, 1)
	slots <- struct{}{}
	var wg sync.WaitGroup
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan bool, 1)
	go func() {
		done <- startJob(ctx, slots, &wg, client.Job{}, func(client.Job) { t.Error("cancelled job started") })
	}()
	cancel()
	if <-done {
		t.Fatal("cancelled job accepted")
	}
	<-slots
	started := make(chan struct{})
	if !startJob(context.Background(), slots, &wg, client.Job{}, func(client.Job) { close(started) }) {
		t.Fatal("job with a free slot was rejected")
	}
	<-started
	wg.Wait()
	if len(slots) != 0 {
		t.Fatal("ordinary job did not release its slot")
	}
}

func TestJitterSpreadsWaits(t *testing.T) {
	seen := map[time.Duration]bool{}
	for i := 0; i < 200; i++ {
		got := jitter(15 * time.Minute)
		if got < 15*time.Minute*3/4 || got > 15*time.Minute*5/4 {
			t.Fatalf("jitter(15m) = %v, want 11m15s to 18m45s", got)
		}
		seen[got] = true
	}
	if len(seen) < 100 {
		t.Fatalf("jitter returned only %d distinct waits; agents would retry together", len(seen))
	}
}
