package main

import (
	"context"
	"log"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/live"
	"github.com/bradhawkins85/myportal-rmm/internal/state"
)

// startLiveSession connects to a live tools session MyPortal listed in the
// job poll, unless this agent is already connected to it. MyPortal keeps
// listing a session until the agent connects, so a failed connection is
// retried on a later poll.
func startLiveSession(ctx context.Context, wg *sync.WaitGroup, active *sync.Map, st state.State, id int64) {
	if _, running := active.LoadOrStore(id, struct{}{}); running {
		return
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		defer active.Delete(id)
		dialCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
		conn, err := live.Dial(dialCtx, st.PortalURL, st.AuthToken, id)
		cancel()
		if err != nil {
			log.Printf("live session %d: could not connect: %v", id, err)
			// Wait before the next poll can retry, so a failing session does
			// not reconnect in a tight loop.
			sleep(ctx, jitter(10*time.Second))
			return
		}
		log.Printf("live session %d: connected", id)
		live.Serve(ctx, conn, id, Version)
		log.Printf("live session %d: ended", id)
	}()
}
