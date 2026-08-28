// Seeded corpus file: Go. Every defect here is INTENTIONAL and catalogued in
// manifest.json by line number. Do not "fix" this file -- it is a measuring
// instrument. See corpus/README.md.

package seeded

import (
	"fmt"
	"os"
	"os/exec"
)

type Job struct {
	dir string
}

func (j *Job) Cancel() {
	_ = os.RemoveAll(j.dir) // SEED: silent-failure, mechanical
}

func sweep(d string) {
	_ = os.RemoveAll(d) // SEED: silent-failure, mechanical
}

func writeState(path string, data []byte) error {
	if err := os.WriteFile(path, data, 0644); err != nil {
		return err
	}
	return nil // CONTROL: error properly returned. NOT a seed.
}

func startDetached(name string) error {
	cmd := exec.Command(name)
	if err := cmd.Start(); err != nil {
		return err
	}
	// CONTROL: discard is deliberate and documented. Start()'s error is already
	// returned above, and this child's exit code is unreliable even on success.
	// A scanner hit here is expected; a REVIEWER who calls it a finding without
	// reading the comment has failed the "different channel / documented
	// discard" rule in SKILL.md check 2.
	go func() { _ = cmd.Wait() }()
	return nil
}

func loadOrDefault(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return "" // SEED: silent-failure, mechanical (no-op err check, nothing logged)
	}
	return string(b)
}

func readManifest(path string) ([]byte, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		// CONTROL (4th false-positive class): real propagation, wrapped
		// across two lines the way gofmt routinely does once the format string
		// plus args stop fitting one line. `return` and `err` are on different
		// source lines here on purpose -- this is the exact shape that was
		// missed before the fix, because the old check searched only the same
		// line as `return`.
		return nil, fmt.Errorf("%w: reading manifest at %s",
			err, path)
	}
	return b, nil
}

func writeManifestOrDrop(path string, data []byte) {
	err := os.WriteFile(path, data, 0644)
	if err != nil {
		// SEED (regression guard for the fix above): genuinely silent, ALSO spanning
		// multiple lines. If the multi-line propagation fix over-reaches, its
		// DOTALL search across this whole body would still need a literal
		// "return ... err" shape to misfire -- there isn't one here, only a
		// discard -- but this stays a multi-line body on purpose so the fix is
		// exercised on the same shape as the real bug, not a simplified one.
		_ = err
	}
}
