package beacon

// BEACON-15 — the artifact cache.
//
//  1. A cache hit hashed the cached file, closed it, then reopened it to copy:
//     the bytes verified were not necessarily the bytes staged. Whoever can
//     write the cache directory swaps the file in between and a step executes
//     it under the pinned digest's name.
//  2. On Windows the cache resolved to the drive-rooted POSIX literal
//     /opt/cortexsim/cache, i.e. C:\opt\cortexsim\cache, which a SYSTEM beacon
//     can create and which inherits "Authenticated Users: Modify" from C:\.

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// The integrity rule for a cache hit: whatever lands at dest has the pinned
// digest, even while something is swapping the cache entry underneath the
// beacon. On the unfixed code roughly one iteration in four stages the swapped
// bytes (verify opened the good file, the copy opened the evil one).
func TestArtifactCache_AStagedFileAlwaysHasThePinnedDigestWhileTheCacheIsSwapped(t *testing.T) {
	good := bytes.Repeat([]byte("GOOD-TOOL-BYTES\n"), 1<<17) // 2 MiB
	evil := bytes.Repeat([]byte("EVIL-TOOL-BYTES\n"), 1<<17) // same size
	srv := newShelfServer(t, good)
	root := t.TempDir()
	cacheDir := filepath.Join(root, "cache")
	stageRoot := filepath.Join(root, "staging")
	if err := os.MkdirAll(cacheDir, 0o755); err != nil {
		t.Fatal(err)
	}

	c := stagingClient(t, srv.URL, stageRoot)
	c.artifactCacheDirFunc = func() string { return cacheDir }
	art := Artifact{Name: "tool.sh", SHA256: digestOf(good), Path: "/api/shelf/payload/tool.sh",
		Dest: filepath.Join(stageRoot, "tool.sh"), Mode: "0755"}
	cached := filepath.Join(cacheDir, strings.ToLower(art.SHA256))

	// The swapper: a local user with write access to the cache directory,
	// flipping the entry between the real tool and their own bytes.
	goodSrc, evilSrc := filepath.Join(root, "good.src"), filepath.Join(root, "evil.src")
	if err := os.WriteFile(goodSrc, good, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(evilSrc, evil, 0o644); err != nil {
		t.Fatal(err)
	}
	var stop atomic.Bool
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		tmp := filepath.Join(cacheDir, "swap.tmp")
		for i := 0; !stop.Load(); i++ {
			src := goodSrc
			if i%2 == 1 {
				src = evilSrc
			}
			_ = os.Remove(tmp)
			if os.Link(src, tmp) == nil {
				_ = os.Rename(tmp, cached)
			}
			time.Sleep(200 * time.Microsecond)
		}
	}()
	defer func() {
		stop.Store(true)
		wg.Wait()
	}()

	for i := 0; i < 40; i++ {
		set := stageOneArtifact(t, c, art)
		if !set.OK() {
			t.Fatalf("iteration %d: staging failed: %+v", i, set.Failures)
		}
		staged, err := os.ReadFile(art.Dest)
		if err != nil {
			t.Fatalf("iteration %d: read staged file: %v", i, err)
		}
		if got := digestOf(staged); got != art.SHA256 {
			t.Fatalf("iteration %d: the staged file's sha256 is %s, the pinned digest is %s (from cache: %v) — "+
				"the cache entry was swapped between the hash and the copy, and a step would have executed it",
				i, shortDigest(got), shortDigest(art.SHA256), set.Files[0].FromCache)
		}
		set.Cleanup(c, "")
	}
}

// An entry that does not hash to its name is dropped, the artifact is fetched
// from SimCore, and what lands at dest is the real tool.
func TestArtifactCache_AnEntryThatDoesNotHashToItsNameIsDiscardedAndRefetched(t *testing.T) {
	good := []byte("#!/bin/sh\necho real tool\n")
	srv := newShelfServer(t, good)
	root := t.TempDir()
	cacheDir := filepath.Join(root, "cache")
	stageRoot := filepath.Join(root, "staging")
	if err := os.MkdirAll(cacheDir, 0o755); err != nil {
		t.Fatal(err)
	}
	c := stagingClient(t, srv.URL, stageRoot)
	c.artifactCacheDirFunc = func() string { return cacheDir }
	art := Artifact{Name: "tool.sh", SHA256: digestOf(good), Path: "/api/shelf/payload/tool.sh",
		Dest: filepath.Join(stageRoot, "tool.sh"), Mode: "0755"}
	cached := filepath.Join(cacheDir, strings.ToLower(art.SHA256))
	if err := os.WriteFile(cached, []byte("#!/bin/sh\necho planted\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	set := stageOneArtifact(t, c, art)
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	defer set.Cleanup(c, "")
	if set.Files[0].FromCache {
		t.Fatal("a planted cache entry was reported as a cache hit")
	}
	if srv.hits.Load() != 1 {
		t.Fatalf("SimCore was contacted %d times, want 1 (the refetch)", srv.hits.Load())
	}
	if staged, _ := os.ReadFile(art.Dest); !bytes.Equal(staged, good) {
		t.Fatalf("staged %q, want the real tool", staged)
	}
	// The planted entry is gone; the cache now holds the verified bytes.
	if now, err := os.ReadFile(cached); err != nil || !bytes.Equal(now, good) {
		t.Fatalf("cache entry after the refetch = %q (err %v), want the verified tool", now, err)
	}
}

// The Windows cache directory rule, as string logic (BEACON-15).
func TestWindowsArtifactCacheDir(t *testing.T) {
	for exe, want := range map[string]string{
		// Service install: the directory the installer strips of inheritance.
		`C:\ProgramData\CortexSim\cortexsim-agent.exe`: `C:\ProgramData\CortexSim\cache`,
		// Per-user install: inside the user's profile.
		`C:\Users\dc\AppData\Local\CortexSim\cortexsim-agent.exe`: `C:\Users\dc\AppData\Local\CortexSim\cache`,
		`D:/tools/cortexsim/cortexsim-agent.exe`:                  `D:\tools\cortexsim\cache`,
		// A binary at a drive root gets no cache: C:\cache inherits the root ACL.
		`C:\cortexsim-agent.exe`: "",
		`C:/cortexsim-agent.exe`: "",
		// Not drive-letter absolute: no cache, never a guess.
		`cortexsim-agent.exe`:                "",
		`\\fileserver\share\cortexsim\a.exe`: "",
		`/opt/cortexsim/bin/cortexsim-agent`: "",
		``:                                   "",
	} {
		if got := windowsArtifactCacheDir(exe); got != want {
			t.Errorf("windowsArtifactCacheDir(%q) = %q, want %q", exe, got, want)
		}
		if got := windowsArtifactCacheDir(exe); strings.EqualFold(got, `C:\opt\cortexsim\cache`) ||
			strings.HasPrefix(got, "/") {
			t.Errorf("windowsArtifactCacheDir(%q) = %q — a drive-rooted POSIX literal", exe, got)
		}
	}
}
