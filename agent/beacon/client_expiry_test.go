package beacon

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestRunExpired_ReturnsWithoutPolling(t *testing.T) {
	for _, longPoll := range []int{0, 5} {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			hits.Add(1)
			t.Errorf("unexpected request %s %s after expiry", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}))
		c := New(srv.URL, "a", 50*time.Millisecond)
		c.LongPollTimeout = longPoll
		c.ExpiresAt = time.Now().Add(-time.Minute)

		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		start := time.Now()
		c.Run(ctx)
		el := time.Since(start)
		cancel()
		srv.Close()
		if el > time.Second {
			t.Fatalf("long-poll=%d: Run took %s with ExpiresAt in the past, want prompt return", longPoll, el)
		}
		if hits.Load() != 0 {
			t.Fatalf("long-poll=%d: %d requests reached SimCore after expiry", longPoll, hits.Load())
		}
	}
}

func TestRunExpiresAtSchedule_PollsThenReturns(t *testing.T) {
	for _, longPoll := range []int{0, 1} {
		var polls atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if strings.HasSuffix(r.URL.Path, "/tasks") {
				polls.Add(1)
				if longPoll > 0 {
					time.Sleep(30 * time.Millisecond)
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"task": nil})
				return
			}
			w.WriteHeader(200)
		}))
		c := New(srv.URL, "a", 20*time.Millisecond)
		c.LongPollTimeout = longPoll
		c.ExpiresAt = time.Now().Add(200 * time.Millisecond)

		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		start := time.Now()
		c.Run(ctx)
		el := time.Since(start)
		ctxErr := ctx.Err()
		cancel()
		srv.Close()
		if ctxErr != nil || el > 2*time.Second {
			t.Fatalf("long-poll=%d: Run returned after %s (ctx err %v), want lifetime expiry within 2s", longPoll, el, ctxErr)
		}
		if polls.Load() == 0 {
			t.Fatalf("long-poll=%d: Run never polled before its expiry", longPoll)
		}
	}
}
