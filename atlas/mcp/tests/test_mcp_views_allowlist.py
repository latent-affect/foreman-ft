"""atlas/GOALS.json C7 (DEVH-49 / SECURITY-PRIVACY-REVIEW.md F1). MCP_VIEWS used to be
ALLOWED_VIEWS minus one excluded name -- a denylist of one, so every future addition to
ALLOWED_VIEWS was published over MCP automatically. Now an explicit literal allowlist. This
suite proves both that the new construction is genuinely independent of ALLOWED_VIEWS (not
just relabeled) and that nothing previously servable was dropped.

    /Users/m5/.venv/bin/python3 -m unittest atlas.mcp.tests.test_mcp_views_allowlist -v
"""
import importlib
import inspect
import sys
import types
import unittest
from unittest import mock

# The 'mcp' SDK installed in this venv (1.29.1) has no mcp.server.mcpserver submodule --
# atlas/mcp/server.py's own docstring says it's meant to run under a separate .venv-mcp
# pinned to the SDK version it needs, which does not exist in this worktree. Pre-existing
# environment gap, unrelated to C7 and out of scope for this fix (C7 is about MCP_VIEWS's
# construction, not the whole server's importability). Stubbed here so the real module can
# still be imported and its real MCP_VIEWS/ALLOWED_VIEWS values exercised, rather than
# falling back to source-text inspection alone.
if "mcp.server.mcpserver" not in sys.modules:
    _stub = types.ModuleType("mcp.server.mcpserver")

    class _StubMCPServer:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

    _stub.MCPServer = _StubMCPServer
    sys.modules["mcp.server.mcpserver"] = _stub

import atlas.mcp.server as mcp_server_module  # noqa: E402
from atlas.query import facade  # noqa: E402


class McpViewsAllowlistTests(unittest.TestCase):
    def test_c7_not_derived_by_subtraction_from_allowed_views(self):
        source = inspect.getsource(mcp_server_module)
        self.assertNotIn("ALLOWED_VIEWS if", source,
                          "MCP_VIEWS must not be computed by filtering ALLOWED_VIEWS")
        self.assertNotIn("ALLOWED_VIEWS -", source)
        self.assertIsInstance(mcp_server_module.MCP_VIEWS, frozenset)

    def test_c7_synthetic_view_added_to_allowed_views_does_not_leak_into_mcp_views(self):
        synthetic = "v_synthetic_test_only_devh49"
        self.assertNotIn(synthetic, facade.ALLOWED_VIEWS)
        patched_allowed = facade.ALLOWED_VIEWS | {synthetic}

        with mock.patch.object(facade, "ALLOWED_VIEWS", patched_allowed):
            reloaded = importlib.reload(mcp_server_module)
            self.assertNotIn(
                synthetic, reloaded.MCP_VIEWS,
                "a view added to ALLOWED_VIEWS must stay invisible to MCP until "
                "deliberately added to MCP_VIEWS's own literal set"
            )

        # restore real module state for later tests / other importers in the same process
        importlib.reload(mcp_server_module)

    def test_c7_regression_every_previously_servable_view_still_servable(self):
        expected = facade.ALLOWED_VIEWS - {"v_fail_open_incident"}
        self.assertEqual(mcp_server_module.MCP_VIEWS, expected)

    def test_c7_payload_bearing_view_still_excluded(self):
        self.assertNotIn("v_fail_open_incident", mcp_server_module.MCP_VIEWS)
        self.assertIn("v_fail_open_incident", facade.ALLOWED_VIEWS)


if __name__ == "__main__":
    unittest.main()
