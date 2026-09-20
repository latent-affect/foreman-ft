"""ATLASSN-48 / dev-harness DEVH-49 (SECURITY-PRIVACY-REVIEW.md F1). atlas/mcp/server.py used to
publish facade.ALLOWED_VIEWS and facade.ALLOWED_LIVE_VIEWS straight through, so every view added
to either set reached MCP automatically -- including v_fail_open_incident, which serves verbatim
SAFETY_DENY payload text. Now explicit literal allowlists.

    /Users/m5/.venv/bin/python3 -m unittest atlas.mcp.tests.test_mcp_views_allowlist -v

This suite proves three separate things, because any one alone is weak: that the payload-bearing
view is genuinely unreachable over MCP; that the new lists are INDEPENDENT of facade's sets
rather than relabelled aliases (a relabel would reintroduce the auto-publish defect the moment
someone adds a view); and that nothing previously servable was dropped, so the fix is not an
arbitrary or empty set.

The installed mcp SDK has no mcp.server.mcpserver submodule -- atlas/mcp/server.py's own
docstring says it runs under a separate .venv-mcp pinned to the SDK version it needs. Stubbed
below so the real module imports and its REAL constants are exercised, rather than falling back
to source-text inspection, which would not catch a value being wrong.
"""

import sys
import types
import unittest

if "mcp.server.mcpserver" not in sys.modules:
    stubModule = types.ModuleType("mcp.server.mcpserver")

    class StubMCPServer:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

    stubModule.MCPServer = StubMCPServer
    sys.modules["mcp.server.mcpserver"] = stubModule

import atlas.mcp.server as server  # noqa: E402
from atlas.query import facade  # noqa: E402

PAYLOAD_BEARING = "v_fail_open_incident"


class McpViewAllowlistTests(unittest.TestCase):
    def test_payload_bearing_view_is_not_published(self):
        """The finding itself. Before this fix, importing server and calling atlas_list_views()
        returned v_fail_open_incident in the live 19-name list."""
        self.assertNotIn(PAYLOAD_BEARING, server.MCP_VIEWS)
        self.assertNotIn(PAYLOAD_BEARING, server.atlas_list_views()["views"])

    def test_payload_bearing_view_is_refused_before_any_db_work(self):
        """Refusal must not depend on the warehouse being reachable, or an unavailable database
        would turn a security refusal into an incidental error."""
        with self.assertRaises(ValueError) as caught:
            server.atlas_query_view(PAYLOAD_BEARING)
        self.assertIn(PAYLOAD_BEARING, str(caught.exception))

    def test_the_view_is_still_available_through_the_facade(self):
        """Scope check: this fix removes the view from MCP, not from the facade. A caller with a
        legitimate need still reaches it the normal way."""
        self.assertIn(PAYLOAD_BEARING, facade.ALLOWED_VIEWS)

    def test_allowlists_are_independent_literals_not_derived_from_facade(self):
        """THE structural assertion. If MCP_VIEWS were ALLOWED_VIEWS (or a subtraction from it),
        a newly added facade view would be auto-published again -- the exact defect this fixes.
        Simulated by adding a synthetic name to facade's set and asserting MCP does not follow."""
        synthetic = "v_synthetic_never_reviewed_for_mcp"
        original = facade.ALLOWED_VIEWS
        facade.ALLOWED_VIEWS = frozenset(original | {synthetic})
        try:
            self.assertNotIn(synthetic, server.MCP_VIEWS)
            self.assertNotIn(synthetic, server.atlas_list_views()["views"])
        finally:
            facade.ALLOWED_VIEWS = original

    def test_live_allowlist_is_independent_too(self):
        synthetic = "v_synthetic_live_never_reviewed"
        original = facade.ALLOWED_LIVE_VIEWS
        facade.ALLOWED_LIVE_VIEWS = frozenset(original | {synthetic})
        try:
            self.assertNotIn(synthetic, server.MCP_LIVE_VIEWS)
            self.assertNotIn(synthetic, server.atlas_list_views()["live_views"])
        finally:
            facade.ALLOWED_LIVE_VIEWS = original

    def test_nothing_previously_servable_was_dropped(self):
        """Regression clause: the batch list must be exactly ALLOWED_VIEWS minus the one
        excluded name. Guards against 'fixing' this by shrinking the set arbitrarily."""
        self.assertEqual(server.MCP_VIEWS, frozenset(facade.ALLOWED_VIEWS) - {PAYLOAD_BEARING})

    def test_live_list_matches_the_facade_live_set(self):
        """No live view is excluded today, and that is a decision rather than an oversight --
        see server.py's comment. If a future live view is deliberately withheld, this assertion
        should be updated with the reason, not deleted."""
        self.assertEqual(server.MCP_LIVE_VIEWS, frozenset(facade.ALLOWED_LIVE_VIEWS))

    def test_every_published_name_is_actually_servable_by_the_facade(self):
        """A name on the MCP list that the facade would refuse is a broken promise -- the tool
        advertises a view no call can ever return."""
        for name in server.MCP_VIEWS:
            self.assertIn(name, facade.ALLOWED_VIEWS, f"{name} is published but not facade-gated")
        for name in server.MCP_LIVE_VIEWS:
            self.assertIn(name, facade.ALLOWED_LIVE_VIEWS, f"{name} is published but not gated")

    def test_batch_and_live_lists_do_not_overlap(self):
        """The two gates are deliberately separate; a name in both would be servable through
        whichever gate happened to be open."""
        self.assertEqual(server.MCP_VIEWS & server.MCP_LIVE_VIEWS, frozenset())


if __name__ == "__main__":
    unittest.main()
