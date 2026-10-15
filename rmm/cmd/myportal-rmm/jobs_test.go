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
