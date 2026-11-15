//go:build windows

package main

import "context"

// startChildReaper is a no-op on Windows: there is no zombie state to collect
// and no subreaper to be.
func startChildReaper(ctx context.Context) {}
