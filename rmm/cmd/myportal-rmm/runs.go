package main

import (
	"context"
	"sort"
	"sync"
)

// runTracker remembers the runs this agent is executing, from collection until
// MyPortal accepts the result. Each job poll sends their IDs, so MyPortal can
// close runs the agent no longer has (after a restart, say) instead of leaving
// them running and holding the device's queue, and stop the runs MyPortal
// aborted.
type runTracker struct {
	mu   sync.Mutex
	runs map[int64]*trackedRun
}

type trackedRun struct {
	cancel  context.CancelFunc
	stopped bool
}

func newRunTracker() *runTracker {
	return &runTracker{runs: map[int64]*trackedRun{}}
}

// add registers a collected run and returns the context to run it with. It
// returns false if the run is already being executed.
func (t *runTracker) add(parent context.Context, id int64) (context.Context, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if _, ok := t.runs[id]; ok {
		return nil, false
	}
	ctx, cancel := context.WithCancel(parent)
	t.runs[id] = &trackedRun{cancel: cancel}
	return ctx, true
}

// done forgets a run once its result is reported (or reporting gave up).
func (t *runTracker) done(id int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if run, ok := t.runs[id]; ok {
		run.cancel()
		delete(t.runs, id)
	}
}

// executing lists the runs MyPortal should still treat as executing. Runs it
// already told the agent to stop are left out, so later polls do not ask for
// them again while the script is being stopped.
func (t *runTracker) executing() []int64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	ids := make([]int64, 0, len(t.runs))
	for id, run := range t.runs {
		if !run.stopped {
			ids = append(ids, id)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

// stop cancels the runs MyPortal aborted, which ends their scripts (and the
// processes they started), and returns the ones that were still executing.
func (t *runTracker) stop(ids []int64) []int64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	var stopped []int64
	for _, id := range ids {
		if run, ok := t.runs[id]; ok && !run.stopped {
			run.stopped = true
			run.cancel()
			stopped = append(stopped, id)
		}
	}
	return stopped
}
