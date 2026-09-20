"""Regression tests for REQ-23's Lesson 17 encoding, agent_registry_staleness_check.py.

Spot-check requirement (REQ-23's own verification text): confirm the encoded check actually
fires under the condition the original lesson describes -- a genuinely stale agent-registry
snapshot -- not just that a comment cites the lesson.

    python3 -m unittest test_agent_registry_staleness_check -v
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import agent_registry_staleness_check as chk


class RecordingHookCommon:
    def __init__(self):
        self.calls = []

    def ask(self, reason):
        self.calls.append(("ask", reason))

    def set_rule(self, rule_id):
        pass


class AgentRegistryStalenessTests(unittest.TestCase):
    def setUp(self):
        self.hc = RecordingHookCommon()
        self.patcher = mock.patch.object(chk, "hc", self.hc)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="req23-lesson17-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _payload(self, subagent_type, transcript_path, cwd=None):
        return {
            "tool_name": "Agent", "cwd": cwd or str(self.root),
            "tool_input": {"subagent_type": subagent_type},
            "transcript_path": str(transcript_path),
        }

    def test_positive_control_edit_after_session_start_fires(self):
        """The real Lesson 17 scenario, reproduced: transcript (session start) exists first,
        then the agent .md file is edited afterward -- this session's own real incident
        tonight (priya-desai.md edited mid-session, REQ-7a)."""
        transcript = self.root / "session.jsonl"
        transcript.write_text("{}\n")
        agents_dir = self.root / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "priya-desai.md"
        agent_md.write_text("---\nname: priya-desai\n---\n")

        # Force a real, observable mtime gap rather than trusting same-second timestamps.
        os_stat = transcript.stat()
        session_start = os_stat.st_mtime
        import os
        os.utime(agent_md, (session_start + 5, session_start + 5))

        with mock.patch.object(chk, "AGENT_DIRS", (agents_dir,)):
            chk.main(self._payload("priya-desai", transcript))

        self.assertEqual(len(self.hc.calls), 1)
        self.assertEqual(self.hc.calls[0][0], "ask")
        self.assertIn("Lesson 17", self.hc.calls[0][1])
        self.assertIn(str(agent_md), self.hc.calls[0][1])

    def test_negative_control_edit_before_session_start_does_not_fire(self):
        """The agent .md file predates the session -- the normal, safe case -- must stay
        silent, or this check would fire on every single dispatch."""
        agents_dir = self.root / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "dana-okafor.md"
        agent_md.write_text("---\nname: dana-okafor\n---\n")

        time.sleep(0.05)
        transcript = self.root / "session.jsonl"
        transcript.write_text("{}\n")

        with mock.patch.object(chk, "AGENT_DIRS", (agents_dir,)):
            chk.main(self._payload("dana-okafor", transcript))

        self.assertEqual(self.hc.calls, [])

    def test_negative_control_no_custom_definition_does_not_fire(self):
        """A built-in agent type (general-purpose, claude) has no .md file to go stale."""
        transcript = self.root / "session.jsonl"
        transcript.write_text("{}\n")
        agents_dir = self.root / "agents"
        agents_dir.mkdir()

        with mock.patch.object(chk, "AGENT_DIRS", (agents_dir,)):
            chk.main(self._payload("general-purpose", transcript))

        self.assertEqual(self.hc.calls, [])

    def test_negative_control_fork_never_fires(self):
        """A fork inherits the live session directly -- it has no separate registry snapshot
        to go stale, so this must never fire for subagent_type 'fork' even if a same-named
        .md file happens to exist."""
        transcript = self.root / "session.jsonl"
        transcript.write_text("{}\n")
        agents_dir = self.root / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "fork.md"
        agent_md.write_text("irrelevant")
        import os
        os.utime(agent_md, (time.time() + 100, time.time() + 100))

        with mock.patch.object(chk, "AGENT_DIRS", (agents_dir,)):
            chk.main(self._payload("fork", transcript))

        self.assertEqual(self.hc.calls, [])

    def test_negative_control_non_agent_tool_not_checked(self):
        chk.main({"tool_name": "Bash", "cwd": str(self.root),
                  "tool_input": {"command": "ls"}, "transcript_path": "/nonexistent"})
        self.assertEqual(self.hc.calls, [])

    def test_missing_transcript_path_fails_open_silently(self):
        """No transcript_path (unexpected payload shape) must not crash or falsely fire --
        NFR-1's fail-toward-safe convention for this ASK-only (non-denying) hook means
        fail-open-silent is correct here, not fail-closed."""
        agents_dir = self.root / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "clint-eastwood.md"
        agent_md.write_text("irrelevant")

        with mock.patch.object(chk, "AGENT_DIRS", (agents_dir,)):
            chk.main({"tool_name": "Agent", "cwd": str(self.root),
                      "tool_input": {"subagent_type": "clint-eastwood"},
                      "transcript_path": None})

        self.assertEqual(self.hc.calls, [])

    def test_project_local_agent_dir_takes_precedence(self):
        """A project-local .claude/agents/<name>.md overrides the global ~/.claude/agents/
        one for resolution purposes -- checked against whichever file would actually load."""
        transcript = self.root / "session.jsonl"
        transcript.write_text("{}\n")
        project_dir = self.root / ".claude" / "agents"
        project_dir.mkdir(parents=True)
        local_md = project_dir / "marcus-webb.md"
        local_md.write_text("local override")
        import os
        session_start = transcript.stat().st_mtime
        os.utime(local_md, (session_start + 10, session_start + 10))

        global_dir = self.root / "global-agents"
        global_dir.mkdir()

        with mock.patch.object(chk, "AGENT_DIRS", (global_dir,)):
            chk.main(self._payload("marcus-webb", transcript, cwd=str(self.root)))

        self.assertEqual(len(self.hc.calls), 1)
        self.assertIn(str(local_md), self.hc.calls[0][1])


if __name__ == "__main__":
    unittest.main()
