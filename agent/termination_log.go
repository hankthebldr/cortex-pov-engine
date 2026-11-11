package main

import (
	"log"
	"os"
	"regexp"
	"strings"
)

// defaultTerminationLog is where Kubernetes reads a container's termination
// message (terminationMessagePolicy: File). CORTEXSIM_TERMLOG overrides it, the
// same variable the pod's start.sh and fetch.sh honour.
const defaultTerminationLog = "/dev/termination-log"

// maxTerminationLine keeps the line well under Kubernetes' 4096-byte cap.
const maxTerminationLine = 1024

var tokenRE = regexp.MustCompile(`cxs_[A-Za-z0-9_\-]+`)

func terminationLogPath() string {
	if p := os.Getenv("CORTEXSIM_TERMLOG"); p != "" {
		return p
	}
	return defaultTerminationLog
}

// fatalLine is the one-line cause written on a fatal startup exit. Any
// enrollment token is reduced to its prefix: nothing on this path should carry
// one, and the termination message is readable by anyone who can describe pods.
func fatalLine(code, reason string) string {
	reason = strings.Join(strings.Fields(reason), " ")
	line := "FATAL " + code + ": " + tokenRE.ReplaceAllString(reason, "cxs_…")
	if len(line) > maxTerminationLine {
		line = strings.ToValidUTF8(line[:maxTerminationLine], "")
	}
	return line
}

// writeTerminationLog writes line to path when that file already exists and is
// writable. Best effort: it never creates the file (off Kubernetes there is
// none) and never fails the caller.
func writeTerminationLog(path, line string) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_TRUNC, 0)
	if err != nil {
		return
	}
	_, _ = f.WriteString(line + "\n")
	_ = f.Close()
}

// exitFatal is a fatal startup exit: the FATAL line on stderr and on the
// termination log, so `kubectl describe pod` shows why the pod is crash-looping.
func exitFatal(code string, err error) {
	log.Printf("FATAL %s: %v", code, err)
	writeTerminationLog(terminationLogPath(), fatalLine(code, err.Error()))
	os.Exit(1)
}
