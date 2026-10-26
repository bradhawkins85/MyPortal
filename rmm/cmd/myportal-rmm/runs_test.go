package main

import (
	"context"
	"reflect"
	"testing"
)

func TestRunTrackerListsStopsAndForgetsRuns(t *testing.T) {
	runs := newRunTracker()
	first, ok := runs.add(context.Background(), 7)
	if !ok {
		t.Fatal("first run rejected")
	}
	second, _ := runs.add(context.Background(), 3)
	if _, ok := runs.add(context.Background(), 7); ok {
		t.Fatal("a run already executing was started again")
	}
	if got := runs.executing(); !reflect.DeepEqual(got, []int64{3, 7}) {
		t.Fatalf("executing = %v", got)
	}
	// Unknown runs are ignored; a stopped run is cancelled once and no longer listed.
	if got := runs.stop([]int64{7, 99}); !reflect.DeepEqual(got, []int64{7}) {
		t.Fatalf("stop = %v", got)
	}
	if first.Err() == nil || second.Err() != nil {
		t.Fatal("stop cancelled the wrong run")
	}
	if got := runs.stop([]int64{7}); got != nil {
		t.Fatalf("second stop = %v", got)
	}
	if got := runs.executing(); !reflect.DeepEqual(got, []int64{3}) {
		t.Fatalf("executing after stop = %v", got)
	}
	runs.done(3)
	runs.done(7)
	if second.Err() == nil {
		t.Fatal("done did not release the run's context")
	}
	if got := runs.executing(); len(got) != 0 {
		t.Fatalf("executing after done = %v", got)
	}
}
