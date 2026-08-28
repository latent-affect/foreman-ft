package seeded

import "testing"

// Controls for the two false-positive classes measured on the hyphy arm of the
// rigor pilot, 2026-08-19. Every case in this file is CORRECT code. Anything
// scan.py reports from here in its MAIN candidate stream is a false positive.
//
// Do not "fix" this file. It is an instrument, like the rest of seeded/.

// CONTROL C05: t.Fatalf both logs the error and aborts the test. Before "fatal"
// was added to SIGNAL_TOKENS, this was reported as "body does not log or
// propagate" -- 6 of 119 unique flagged items on the hyphy arm were this gap.
func TestControlFatalfIsHandling(t *testing.T) {
	err := doThing()
	if err != nil {
		t.Fatalf("doThing: %v", err)
	}
}

// CONTROL C05b: the non-test spelling. A package-level fatal() helper that
// logs and exits is handling, not silence.
//
// The message deliberately contains NO signal word. An earlier draft read
// "doThing failed and we are stopping", and that control passed even against the
// unpatched scanner -- because signal detection tokenises the whole body
// including string-literal contents, so the word "failed" inside the message was
// read as handling. A control that passes for the wrong reason measures nothing.
// Tracked separately: literal prose counting as signal is its own false-negative
// source, since `if err != nil { note("this failed") }` scores as handled.
func controlFatalHelperIsHandling() {
	err := doThing()
	if err != nil {
		fatal("doThing: %v", err)
	}
}

// CONTROL C06: an implementation marker appearing ONLY inside a comment, where
// the prose describes the design and the code does the opposite. This is the
// class that flagged a comment reading "-- no mocks" for the word "mock".
// Expected: diverted to the comment-prose stream, absent from the main list.
// this client speaks the real wire protocol directly, with no mocks and nothing
// hardcoded; the fallback path was deliberately removed
func controlCommentProseOnly() error {
	return doThing()
}

// SEED S08 (check 3, mechanical): a DEFERRAL marker in a comment must STAY in
// the main stream. Deferral markers live in comments by nature, so the
// comment-prose split must not swallow them -- that would trade one false
// positive class for a false negative, which is the worse direction.
// TODO: this pane sizing is wrong for tabs and needs a real fix
func seedDeferralMarkerStaysVisible() {}

// CONTROL C07: code on the line as well as the comment, so it is NOT
// comment-only and must remain a real main-stream hit.
var controlMarkerOnCodeLine = getFallbackValue() // mock wired in here on purpose

// SEED S10 (check 2, mechanical): a genuinely silent handler whose MESSAGE merely
// mentions failure. Signal detection tokenises the whole body, so before string
// literal contents were stripped, the word "failed" inside the message scored this
// as handled. A false negative in the false-negative detector.
func seedSilentButMessageMentionsFailure() {
	err := doThing()
	if err != nil {
		note("this failed and nobody will ever know")
	}
}

// CONTROL C07b: real handling through an identifier. Must not regress to a hit
// when string literals are stripped.
func controlRealHandlingViaIdentifier() {
	err := doThing()
	if err != nil {
		log.Printf("doThing: %v", err)
	}
}

// CONTROL C08: a marker word must not match inside a longer word that merely
// contains its letters. Plain substring matching flagged this class of disclosure
// string as a proxy marker. Explanatory text here deliberately avoids quoting any
// marker word, because the corpus grader has a +/-3 line tolerance and a quoted
// marker in an adjacent comment collides with the control it is describing.
var controlScannedIsNotCanned = "[NOT SCANNED -- could not read]"

//
//
//
//
// SEED S11 (check 3, mechanical): the plural form must still match. The leading-only
// word boundary exists so that fixing the case above does not trade it for a miss.
// TODOs remain outstanding here
