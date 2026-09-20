"""ATLASSN-61 / FORE-277: a SEEDED battery for the bash_command_shape extractor, measuring
RECALL against known-hard inputs rather than only precision against synthetic ones.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_bash_shape_seeded_battery -v

Why this exists alongside test_bash_shape.py, which already pairs a positive and a negative per
feature. Those prove each regex fires on the shape it names. They do not answer the question that
decides whether this dataset is worth anything: a classifier that labels almost everything
"nothing here" scores extremely well against a corpus that is almost entirely ordinary, and its
score says nothing about whether it would catch the cases the dataset was built to reach.
TESS-104's pattern is the fix -- seed cases whose answer is known, and measure how many come back.

Every seeded positive below is written to LOOK ORDINARY. A case that announces itself tests
nothing; the interesting question is whether an obfuscated form still trips the shape it belongs
to. The benign half is drawn from patterns the real corpus is full of (heredocs into python,
`python3 -c` with json, git plumbing) so the false-positive count is measured against traffic that
actually occurs rather than against strawmen.

This is an INSTRUMENT CHARACTERISATION, not a pass/fail gate on the corpus. It says how well the
extractor sees; ARCHITECTURE.md section 24.2 is explicit that recording a shape is not a verdict
about it, and section 24.5 that the deliverable is a base-rate table rather than a rule.
"""

import unittest

from atlas.warehouse.bash_shape import extract_shape

# (label, command, feature that MUST fire). Each is a real obfuscation shape, written the way
# someone would write it if they were not trying to be noticed.
SEEDED_POSITIVES = [
    ("base64 payload piped to a shell",
     "echo aWQ7IHdob2FtaQ== | base64 -d | sh",
     "has_base64_decode"),
    ("base64 with the long flag, buried mid-pipeline",
     "cat /tmp/blob.txt | tr -d '\\n' | base64 --decode > /tmp/out.sh",
     "has_base64_decode"),
    ("chr()-assembled string inside an otherwise ordinary one-liner",
     "python3 -c \"import os; p=''.join(chr(c) for c in [47,101,116,99]); print(os.listdir(p))\"",
     "has_chr_paren"),
    ("getattr on __import__, spaced out to look like formatting",
     "python3 -c \"getattr( __import__('os'), 'system')('id')\"",
     "has_getattr_dunder_import"),
    ("import name built by concatenation",
     "python3 -c \"__import__('o'+'s').getcwd()\"",
     "has_string_concat_import"),
    ("eval on a command substitution",
     "eval \"$(cat /tmp/payload)\"",
     "has_eval_word"),
    ("command name held in a variable",
     "$CMD --quiet /tmp/target",
     "has_dollar_var_command_position"),
    ("braced variable in command position",
     "${RUNNER} install --force",
     "has_dollar_var_command_position"),
    ("heredoc into an interpreter rather than a file",
     "python3 <<'PY'\nimport os\nos.system('id')\nPY",
     "has_heredoc_into_interpreter"),
    ("python -c reaching subprocess",
     "/Users/m5/.venv/bin/python3 -c \"import subprocess; subprocess.run(['id'])\"",
     "has_python_c_os_subprocess"),
    ("python -c reaching os.system",
     "python3 -c \"import os; os.system('id')\"",
     "has_python_c_os_subprocess"),
    ("unbalanced quote, which is itself the signal",
     "echo 'unterminated",
     "parse_failure"),
    ("assign-then-invoke past an earlier, unrelated statement",
     "cd /tmp/workdir && RUNNER=deploy_script && $RUNNER --env prod",
     "has_cmd_pos_var_indirection"),
    ("concatenated brace-variable invocation",
     "a=cur; b=l; ${a}${b} https://example.internal/artifact.tar.gz",
     "has_cmd_pos_var_indirection"),
]

# Shapes the real corpus is full of. None of these should trip the feature named beside them --
# the point is that the extractor's positives are not bought by flagging ordinary work.
SEEDED_NEGATIVES = [
    ("ordinary git status", "git status --short", None),
    ("heredoc into a FILE, not an interpreter",
     "cat > /tmp/notes.md <<'EOF'\nsome text\nEOF", "has_heredoc_into_interpreter"),
    ("python -c doing harmless json work",
     "python3 -c \"import json; print(json.load(open('/tmp/x.json'))['k'])\"",
     "has_python_c_os_subprocess"),
    ("base64 ENCODING, not decoding", "base64 /tmp/in > /tmp/out.b64", "has_base64_decode"),
    ("a word containing eval as a substring",
     "./scripts/evaluate_corpus.sh --all", "has_eval_word"),
    ("a variable as an ARGUMENT, not in command position",
     "grep -n TODO \"$FILE\"", "has_dollar_var_command_position"),
    ("getattr that has nothing to do with __import__",
     "python3 -c \"print(getattr(obj, 'name', None))\"", "has_getattr_dunder_import"),
    ("a plain import with no concatenation",
     "python3 -c \"__import__('os').getcwd()\"", "has_string_concat_import"),
    ("a word starting with chr", "echo chronology >> /tmp/log", "has_chr_paren"),
    ("interpreter named but no heredoc",
     "which python3 && python3 --version", "has_heredoc_into_interpreter"),
    ("an ordinary quoted commit message, not a quote-splice",
     'git commit -m "fix: rm handling"', "has_quote_splice"),
    ("a quoted assignment with a space, not a quote-splice",
     'FILE="/tmp/a b/c.txt"; cat "$FILE"', "has_quote_splice"),
    ("a variable used as an ARGUMENT after a real command, not in command position",
     'cd /tmp && echo "$HOME/project"', "has_cmd_pos_var_indirection"),
    ("awk's own $1, not a shell variable in command position",
     "awk '{print $1}' file.txt", "has_cmd_pos_var_indirection"),
]


class SeededRecallTests(unittest.TestCase):
    def test_every_seeded_positive_is_caught(self):
        """RECALL. Reported as a rate with the misses named, not as a bare assertion, because
        "12 of 12" and "11 of 12, and here is the one that got through" are different findings and
        only the second is actionable."""
        missed = []
        for label, command, feature in SEEDED_POSITIVES:
            if not extract_shape(command).get(feature):
                missed.append(f"{feature}: {label}")
        rate = (len(SEEDED_POSITIVES) - len(missed)) / len(SEEDED_POSITIVES)
        self.assertEqual(
            missed, [],
            f"recall {rate:.0%} ({len(SEEDED_POSITIVES) - len(missed)} of "
            f"{len(SEEDED_POSITIVES)}); missed: {missed}")

    def test_no_seeded_negative_trips_its_lookalike_feature(self):
        """The other half. Recall bought by flagging everything is not recall, and the corpus is
        overwhelmingly made of exactly these shapes."""
        false_positives = []
        for label, command, feature in SEEDED_NEGATIVES:
            if feature is None:
                fired = [k for k, v in extract_shape(command).items()
                         if k != "token_count" and v]
                if fired:
                    false_positives.append(f"{label}: fired {fired}")
            elif extract_shape(command).get(feature):
                false_positives.append(f"{feature}: {label}")
        self.assertEqual(false_positives, [], f"false positives: {false_positives}")

    def test_the_battery_covers_every_feature_the_extractor_emits(self):
        """A battery that silently stops covering a feature is worse than no battery, because it
        still reports a recall number. Derived from the extractor's own output keys rather than a
        hand-maintained list, so a feature added there fails here until it is seeded.

        has_quote_splice is the one deliberate exception: ATLASSN-95 pulled it after it failed its
        own frozen real-corpus false-positive gate (5.13% measured vs a 0.5% threshold), so it is
        hardcoded to 0 in extract_shape() and can never have a real seeded positive -- see
        bash_shape.py's module-level comment for the measured numbers and root cause."""
        emitted = {k for k in extract_shape("true") if k != "token_count"}
        seeded = {feature for _, _, feature in SEEDED_POSITIVES} | {"has_quote_splice"}
        self.assertEqual(emitted - seeded, set(),
                         f"features with no seeded positive: {sorted(emitted - seeded)}")

    def test_has_quote_splice_is_hardcoded_zero_regardless_of_shape(self):
        """ATLASSN-95: Feature B (has_quote_splice) was pulled after failing C4 on the real
        corpus. Proves the disablement actually holds -- these are the exact shapes the original
        feature was designed to catch, and none of them may ever fire again."""
        pulled_feature_shapes = [
            'su""do systemctl restart nginx',
            "rm -r\\f /tmp/build-artifacts",
            "r''m -rf /tmp/x",
        ]
        fired = [c for c in pulled_feature_shapes if extract_shape(c).get("has_quote_splice")]
        self.assertEqual(fired, [], f"has_quote_splice fired on: {fired} -- it must stay 0")

    def test_an_all_clear_classifier_would_fail_this_battery(self):
        """The control on the control. If a stub that reports nothing-ever could pass the recall
        test, that test proves nothing about the real extractor -- so assert directly that the
        battery discriminates."""
        def all_clear(command):
            return {k: 0 for k in extract_shape("true")}
        missed = [f for _, cmd, f in SEEDED_POSITIVES if not all_clear(cmd).get(f)]
        self.assertEqual(len(missed), len(SEEDED_POSITIVES),
                         "a nothing-ever classifier passed some seeded positives -- the battery "
                         "is not measuring what it claims to")


if __name__ == "__main__":
    unittest.main()
