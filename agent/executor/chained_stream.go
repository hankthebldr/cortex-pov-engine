package executor

// lineStream hands one stream of a chained step to an OutputChunkFunc, a line
// at a time, as the anchor prints it.
//
// The anchor prints a newline before each sentinel so the sentinel always
// starts a line (script.go). For output that already ended in a newline that
// shows up as one blank line just before the sentinel, which the step did not
// print. A blank line is therefore held until something follows it, and the
// last one before the sentinel is dropped.
type lineStream struct {
	onChunk OutputChunkFunc
	stderr  bool
	blank   int // blank lines seen and not yet handed on
}

// line takes one line of the step's output (without its line ending).
func (l *lineStream) line(text string) {
	if l.onChunk == nil {
		return
	}
	if text == "" {
		l.blank++
		return
	}
	l.flush(0)
	l.onChunk(text+"\n", l.stderr)
}

// end is called when the stream reaches its sentinel (or the step is cut short).
func (l *lineStream) end() {
	if l.onChunk != nil {
		l.flush(1)
	}
}

// flush hands on the held blank lines except the last keep.
func (l *lineStream) flush(keep int) {
	for ; l.blank > keep; l.blank-- {
		l.onChunk("\n", l.stderr)
	}
	l.blank = 0
}
