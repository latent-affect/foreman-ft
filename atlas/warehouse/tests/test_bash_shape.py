"""ATLASSN-61. Proves bash_shape.extract_shape() discriminates -- every feature has a paired
positive AND negative fixture, not just a positive one, per the standing rule that a self-written
detector must be shown to correctly NOT fire on a deliberately similar-but-clean input before its
firing result is trusted.

    /Users/m5/.venv/bin/python3 -m unittest atlas.warehouse.tests.test_bash_shape -v
"""

import unittest

from atlas.warehouse.bash_shape import extract_shape

ALL_FLAGS = (
    "has_chr_paren",
    "has_base64_decode",
    "has_eval_word",
    "has_getattr_dunder_import",
    "has_string_concat_import",
    "has_dollar_var_command_position",
    "has_heredoc_into_interpreter",
    "has_python_c_os_subprocess",
)


class BashShapeTests(unittest.TestCase):
    def _assert_only(self, command, flag, parse_failure=0):
        """Asserts exactly one flag fires (or none, if flag is None) and no others do -- a
        detector that fires on everything is as useless as one that fires on nothing, and this
        catches that failure mode directly rather than checking only the target flag."""
        features = extract_shape(command)
        self.assertEqual(features["parse_failure"], parse_failure, features)
        for f in ALL_FLAGS:
            expected = 1 if f == flag else 0
            self.assertEqual(features[f], expected, f"{f} wrong for {command!r}: {features}")

    # ---------------------------------------------------------------- chr(

    def test_chr_paren_positive(self):
        self._assert_only('python3 -c "print(chr(65))"', "has_chr_paren")

    def test_chr_paren_negative_similar_word(self):
        # "char" and "chr" as a bare identifier (no open-paren) must not trip a substring match.
        self._assert_only("echo characteristic chr_value", None)

    # ---------------------------------------------------------------- base64 -d

    def test_base64_decode_positive(self):
        self._assert_only("echo aGVsbG8= | base64 -d", "has_base64_decode")

    def test_base64_decode_positive_long_flag(self):
        self._assert_only("base64 --decode file.b64", "has_base64_decode")

    def test_base64_decode_negative_encode(self):
        # Encoding (the default/no -d) is common and legitimate -- must not trip.
        self._assert_only("echo hello | base64", None)

    # ---------------------------------------------------------------- eval

    def test_eval_word_positive(self):
        self._assert_only('eval "$(cat script.sh)"', "has_eval_word")

    def test_eval_word_negative_substring(self):
        # "evaluate" / "retrieval" contain "eval" as a substring but are not the eval builtin.
        self._assert_only("python3 evaluate.py --retrieval-mode", None)

    # ---------------------------------------------------------------- getattr(__import__

    def test_getattr_dunder_import_positive(self):
        self._assert_only(
            'python3 -c "getattr(__import__(\'os\'), \'system\')(\'ls\')"',
            "has_getattr_dunder_import",
        )

    def test_getattr_dunder_import_negative_unrelated_getattr(self):
        self._assert_only('python3 -c "getattr(obj, \'attr\')"', None)

    # ---------------------------------------------------------------- string concat in import

    def test_string_concat_import_positive(self):
        self._assert_only(
            'python3 -c "__import__(\'o\' + \'s\')"', "has_string_concat_import"
        )

    def test_string_concat_import_negative_plain_import(self):
        self._assert_only('python3 -c "__import__(\'os\')"', None)

    # ---------------------------------------------------------------- $VAR in command position

    def test_dollar_var_command_position_positive(self):
        self._assert_only('$RUNNER "$@"', "has_dollar_var_command_position")

    def test_dollar_var_command_position_positive_braced(self):
        self._assert_only("${RUNNER} arg1", "has_dollar_var_command_position")

    def test_dollar_var_command_position_negative_var_as_argument(self):
        # $FOO appears, but not in command position (argv[0]) -- this is the exact false-alarm
        # shape the feature is supposed to avoid.
        self._assert_only('echo "$FOO"', None)

    # ---------------------------------------------------------------- heredoc into interpreter

    def test_heredoc_into_interpreter_positive(self):
        self._assert_only("python3 <<EOF\nprint(1)\nEOF", "has_heredoc_into_interpreter")

    def test_heredoc_into_interpreter_negative_heredoc_into_cat(self):
        # A heredoc feeding a non-interpreter (cat, tee) is common for writing files and must
        # not trip this flag.
        self._assert_only("cat <<EOF > out.txt\nhello\nEOF", None)

    def test_heredoc_into_interpreter_negative_interpreter_mentioned_no_heredoc(self):
        # Interpreter word present, but no heredoc operator at all.
        self._assert_only("python3 script.py", None)

    # ---------------------------------------------------------------- python -c os.system/subprocess

    def test_python_c_os_system_positive(self):
        self._assert_only('python3 -c "import os; os.system(\'ls\')"', "has_python_c_os_subprocess")

    def test_python_c_subprocess_positive(self):
        self._assert_only(
            'python3 -c "import subprocess; subprocess.run([\'ls\'])"',
            "has_python_c_os_subprocess",
        )

    def test_python_c_negative_harmless_code(self):
        # python -c is extremely common in honest work (FORE-277's own toy numbers: 13% of the
        # corpus) -- must only trip when the inline code actually touches os.system/subprocess.
        self._assert_only('python3 -c "print(1 + 1)"', None)

    def test_python_c_negative_wrong_binary(self):
        # "-c" is also a common flag for other tools (e.g. curl, grep) -- must not trip just
        # because the previous token happens to end in "-c".
        self._assert_only("grep -c pattern file.txt", None)

    # ---------------------------------------------------------------- parse_failure

    def test_parse_failure_positive_unbalanced_quote(self):
        features = extract_shape("echo 'unbalanced")
        self.assertEqual(features["parse_failure"], 1)
        self.assertIsNone(features["token_count"])

    def test_parse_failure_negative_balanced_command(self):
        features = extract_shape("git status")
        self.assertEqual(features["parse_failure"], 0)
        self.assertEqual(features["token_count"], 2)

    def test_parse_failure_still_detects_raw_text_flags(self):
        # A parse failure must not silently zero out flags that don't depend on tokenization --
        # documented in extract_shape's fallback branch, checked here rather than assumed.
        features = extract_shape("python3 -c 'print(chr(65))")
        self.assertEqual(features["parse_failure"], 1)
        self.assertEqual(features["has_chr_paren"], 1)

    # ---------------------------------------------------------------- clean, real-world commands

    def test_clean_git_status_trips_nothing(self):
        self._assert_only("git status", None)

    def test_clean_python_c_with_json_trips_nothing(self):
        self._assert_only('python3 -c "import json; print(json.dumps({}))"', None)

    def test_clean_heredoc_to_file_trips_nothing(self):
        self._assert_only("tee config.yaml <<EOF\nkey: value\nEOF", None)


if __name__ == "__main__":
    unittest.main()
