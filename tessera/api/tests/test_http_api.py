import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ..http_api import build_server
from ...gitops.gitops import GitOps
from ...store.store import Store


def lan_ip():
    """A real, non-loopback local interface address, for the localhost-only bind test."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["TESSERA_ENABLE_SQL"] = "1"
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)
        self.store = Store(root / "test.db", codename="TESTPROJ", prefix="TP")
        self.gitops = GitOps(self.store, root / "stages")
        self.docs_root = root / "docs"
        self.docs_root.mkdir()
        self.server = build_server(self.store, self.gitops, self.docs_root)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp_dir.cleanup()

    def request_json(self, method, path, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_core_endpoints_roundtrip(self):
        status, body = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Task", "reporter": "me", "actor": "agent"},
        )
        self.assertEqual(status, 201)
        tid = body["ticket_id"]

        status, body = self.request_json("GET", f"/tickets/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(body["ticket_id"], tid)

        status, body = self.request_json("GET", "/tickets")
        self.assertEqual(status, 200)
        self.assertIn(tid, [t["ticket_id"] for t in body["tickets"]])

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/comments", {"actor": "agent", "body": "hi"}
        )
        self.assertEqual(status, 200)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/criteria",
            {"actor": "agent", "criteria": [
                {"id": "c1", "statement": "works", "verification": "manual", "verifiable": True}
            ]},
        )
        self.assertEqual(status, 200)
        self.assertIn("criteria_hash", body)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/transition", {"actor": "agent", "status": "in_progress"}
        )
        self.assertEqual(status, 200)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/fields",
            {"actor": "agent", "field_name": "sprint", "field_value": 7},
        )
        self.assertEqual(status, 200)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/reference_docs",
            {"actor": "agent", "paths": ["docs/x.md"]},
        )
        self.assertEqual(status, 200)

    def test_binds_localhost_only(self):
        lan_ip_value = lan_ip()
        if lan_ip_value is None:
            self.skipTest("no outbound network route available to determine a LAN IP")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            with self.assertRaises((ConnectionRefusedError, socket.timeout, OSError)):
                sock.connect((lan_ip_value, self.port))
        finally:
            sock.close()
        # And loopback DOES work, so this isn't just "the port is closed everywhere".
        status, _ = self.request_json("GET", "/tickets")
        self.assertEqual(status, 200)

    def test_status_codes_distinct_and_correct(self):
        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
        )
        self.assertEqual(status, 201)

        status, body = self.request_json("GET", "/tickets/TP-999999")
        self.assertEqual(status, 404)

        _, created = self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
        )
        tid = created["ticket_id"]
        status, body = self.request_json(
            "POST", f"/tickets/{tid}/transition", {"actor": "agent", "status": "in_review"}
        )
        self.assertEqual(status, 400)
        self.assertIn("illegal transition", body["error"])

        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Sub-task", "reporter": "me", "actor": "agent"}
        )
        self.assertEqual(status, 400)
        self.assertIn("Sub-task", body["error"])

    def test_actor_required_not_defaulted(self):
        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me"}
        )
        self.assertEqual(status, 400)
        self.assertIn("actor", body["error"])

    def test_idempotency_key_passthrough(self):
        status1, body1 = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Task", "reporter": "me", "actor": "agent",
             "idempotency_key": "fixed-key"},
        )
        status2, body2 = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Task", "reporter": "me", "actor": "agent",
             "idempotency_key": "fixed-key"},
        )
        self.assertEqual(status1, 201)
        self.assertEqual(status2, 201)
        self.assertEqual(body1["ticket_id"], body2["ticket_id"])

    def test_comment_code_snippet_passthrough(self):
        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"},
        )
        tid = body["ticket_id"]

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/comments",
            {"actor": "agent", "body": "fixed it", "code_snippet": "x = 1"},
        )
        self.assertEqual(status, 200)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/comments", {"actor": "agent", "body": "plain"},
        )
        self.assertEqual(status, 200)

        status, body = self.request_json("GET", f"/tickets/{tid}")
        comments = body["comments"]
        self.assertEqual(comments[0]["code_snippet"], "x = 1")
        self.assertIsNone(comments[1]["code_snippet"])

    def test_get_query_string_actually_parsed(self):
        # code-review finding: do_GET always dispatched with body={}, so a GET route
        # handler reading a query param (list_tickets' status/assignee/type, or
        # discrepancy's stage) always saw nothing no matter what the URL said.
        self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
        )
        self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Bug", "reporter": "me", "actor": "agent", "assignee": "alice"},
        )
        status, body = self.request_json("GET", "/tickets?type=Bug")
        self.assertEqual(status, 200)
        self.assertTrue(body["tickets"])
        self.assertTrue(all(t["type"] == "Bug" for t in body["tickets"]))

        status, body = self.request_json("GET", "/tickets?assignee=alice")
        self.assertEqual(status, 200)
        self.assertTrue(all(t["assignee"] == "alice" for t in body["tickets"]))

    def test_compact_query_param_omits_null_and_empty_fields(self):
        # HTTP mirror of the CLI's --compact flag.
        status, body = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Task", "reporter": "me", "actor": "agent", "summary": "x"},
        )
        self.assertEqual(status, 201)
        tid = body["ticket_id"]

        status, full = self.request_json("GET", f"/tickets/{tid}")
        self.assertEqual(status, 200)
        self.assertIn("assignee", full)
        self.assertIsNone(full["assignee"])

        status, got = self.request_json("GET", f"/tickets/{tid}?compact=1")
        self.assertEqual(status, 200)
        self.assertNotIn("assignee", got)
        self.assertNotIn("reference_docs", got)
        self.assertEqual(got["ticket_id"], tid)
        self.assertEqual(got["summary"], "x")

        status, listed = self.request_json("GET", "/tickets?compact=1")
        self.assertEqual(status, 200)
        matched = [t for t in listed["tickets"] if t["ticket_id"] == tid]
        self.assertEqual(len(matched), 1)
        self.assertNotIn("assignee", matched[0])

        # without the param, both endpoints stay full-fidelity by default
        status, listed_full = self.request_json("GET", "/tickets")
        matched_full = [t for t in listed_full["tickets"] if t["ticket_id"] == tid]
        self.assertIn("assignee", matched_full[0])

    def test_severity_max_query_param_is_a_threshold(self):
        # HTTP mirror of the CLI's --severity-max.
        self.request_json("POST", "/tickets", {
            "ticket_type": "Bug", "reporter": "me", "actor": "agent", "severity": 0,
        })
        self.request_json("POST", "/tickets", {
            "ticket_type": "Bug", "reporter": "me", "actor": "agent", "severity": 3,
        })
        self.request_json("POST", "/tickets", {
            "ticket_type": "Bug", "reporter": "me", "actor": "agent",
        })

        status, body = self.request_json("GET", "/tickets?severity_max=1")
        self.assertEqual(status, 200)
        severities = [t["severity"] for t in body["tickets"]]
        self.assertIn(0, severities)
        self.assertNotIn(3, severities)
        self.assertNotIn(None, severities)

        status, body = self.request_json("GET", "/tickets?severity_max=bogus")
        self.assertEqual(status, 400)

    def test_retry_exhaustion_maps_to_503_not_uncaught_crash(self):
        import sqlite3
        from unittest import mock

        with mock.patch.object(
            self.store, "retry_internal",
            side_effect=sqlite3.OperationalError("database is locked (simulated)"),
        ):
            status, body = self.request_json(
                "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
            )
        self.assertEqual(status, 503)
        self.assertIn("error", body)

    def test_non_transient_operational_error_maps_to_500_not_503(self):
        # Second review-pass finding: a blanket sqlite3.OperationalError -> 503 catch
        # would also mislabel a genuine, permanent bug (bad SQL, on-disk corruption) as
        # "transient, retry it". Only a locked/busy message -- the same classification
        # retry_internal itself uses -- should map to 503; anything else is a real 500.
        import sqlite3
        from unittest import mock

        with mock.patch.object(
            self.store, "retry_internal",
            side_effect=sqlite3.OperationalError("no such column: bogus (simulated)"),
        ):
            status, body = self.request_json(
                "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
            )
        self.assertEqual(status, 500)
        self.assertIn("error", body)

    def test_sql_query_endpoint_roundtrip_and_rejects_writes(self):
        self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
        )
        status, body = self.request_json("POST", "/sql", {"query": "SELECT ticket_id FROM tickets"})
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"], [["TP-1"]])

        status, body = self.request_json(
            "POST", "/sql", {"query": "DELETE FROM tickets WHERE ticket_id='TP-1'"}
        )
        self.assertEqual(status, 400)

    def test_sql_query_self_healing_via_http(self):
        # A trailing comma is fixed and the response discloses it -- full
        # transparency, not a silent rewrite, per the operator's own requirement.
        self.request_json(
            "POST", "/tickets", {"ticket_type": "Task", "reporter": "me", "actor": "agent"}
        )
        status, body = self.request_json(
            "POST", "/sql", {"query": "SELECT ticket_id, FROM tickets"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["self_healed"])
        self.assertIn("removed a trailing comma", body["fixes_applied"])
        self.assertIsNotNone(body["original_error"])
        self.assertEqual(body["rows"], [["TP-1"]])

        # A normal, already-correct query is untouched and discloses no healing.
        status, body = self.request_json("POST", "/sql", {"query": "SELECT ticket_id FROM tickets"})
        self.assertEqual(status, 200)
        self.assertFalse(body["self_healed"])
        self.assertEqual(body["fixes_applied"], [])

    def test_sql_query_multi_statement_does_not_crash_the_connection(self):
        # sqlite3.Warning is not a DatabaseError subclass and previously escaped the
        # /sql route entirely uncaught, crashing the request thread and dropping the
        # connection with no HTTP response at all -- found by direct repro, not by
        # inspection. Must come back as a clean 400.
        status, body = self.request_json("POST", "/sql", {"query": "SELECT 1; SELECT 2"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_sql_query_blob_value_does_not_crash_json_response(self):
        # bytes returned from randomblob()/a BLOB column crashed json.dumps in
        # respond_internal() uncaught, dropping the connection with no response at
        # all -- found by direct repro, not by inspection.
        status, body = self.request_json("POST", "/sql", {"query": "SELECT randomblob(8)"})
        self.assertEqual(status, 200)
        self.assertIsInstance(body["rows"][0][0], str)

    def test_sql_schema_endpoint(self):
        status, body = self.request_json("GET", "/sql/schema")
        self.assertEqual(status, 200)
        self.assertIn("tickets", body["tables"])
        col_names = {c["name"] for c in body["tables"]["tickets"]}
        self.assertIn("summary", col_names)

    def test_sql_schema_endpoint_includes_column_descriptions(self):
        status, body = self.request_json(
            "POST", "/sql/schema/tickets/severity/description",
            {"description": "S0-S4, lower is worse", "actor": "agent"},
        )
        self.assertEqual(status, 200)

        status, body = self.request_json("GET", "/sql/schema")
        self.assertEqual(status, 200)
        self.assertEqual(body["descriptions"]["tickets"]["severity"], "S0-S4, lower is worse")

    def test_sql_query_bad_table_name_not_misclassified_as_contention(self):
        # Per Clint Eastwood's review, confirmed real: a SQL typo raises the SAME
        # sqlite3.OperationalError type retry_internal's 503 classifier looks for, so a
        # query against a table literally named "locked" would be misreported as store
        # contention if this route let the error fall through to dispatch()'s generic
        # handler instead of catching it inside the /sql route itself.
        status, body = self.request_json("POST", "/sql", {"query": "SELECT * FROM locked"})
        self.assertEqual(status, 400)
        self.assertIn("no such table", body["error"])

    def test_sql_disabled_without_env_flag(self):
        os.environ.pop("TESSERA_ENABLE_SQL", None)
        try:
            status, body = self.request_json("POST", "/sql", {"query": "SELECT 1"})
        finally:
            os.environ["TESSERA_ENABLE_SQL"] = "1"
        self.assertEqual(status, 403)
        self.assertIn("disabled", body["error"])

    def test_cross_origin_post_rejected(self):
        # A POST + Content-Type: text/plain (both CORS-safelisted) from a
        # malicious webpage the operator's browser has open never triggers a preflight
        # -- reproduced live before the fix (real ticket created, no CORS headers in the
        # response). The real app.js client never sends Origin at all for same-origin
        # requests in this test harness's urllib client, so this exercises the exact
        # exploit shape, not just any Origin header.
        url = f"http://127.0.0.1:{self.port}/tickets"
        data = json.dumps({"ticket_type": "Task", "reporter": "me", "actor": "agent"}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "text/plain")
        req.add_header("Origin", "http://evil.example.com")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status, body = e.code, json.loads(e.read())
        self.assertEqual(status, 403)
        self.assertNotIn("ticket_id", body)

    def test_same_origin_post_with_no_origin_header_still_works(self):
        # The real app.js client (and this test harness's own request_json helper) never
        # sends an Origin header for a same-origin request -- confirm the fix doesn't
        # break the legitimate case, only the spoofed cross-origin one.
        status, body = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Task", "reporter": "me", "actor": "agent"},
        )
        self.assertEqual(status, 201)

    def test_post_with_matching_origin_still_works(self):
        # A same-origin request where the browser DOES set Origin (some browsers do,
        # for state-changing methods, even same-origin) must still succeed -- only a
        # MISMATCHING Origin is rejected.
        url = f"http://127.0.0.1:{self.port}/tickets"
        data = json.dumps({"ticket_type": "Task", "reporter": "me", "actor": "agent"}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", f"http://127.0.0.1:{self.port}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status, body = resp.status, json.loads(resp.read())
        self.assertEqual(status, 201)

    def test_non_json_content_type_rejected(self):
        # Defense in depth: even with no Origin header at all, a non-JSON
        # Content-Type on a write is rejected -- closes the CORS-safelisted
        # content-type gap directly, not just via the Origin check.
        url = f"http://127.0.0.1:{self.port}/tickets"
        data = json.dumps({"ticket_type": "Task", "reporter": "me", "actor": "agent"}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "text/plain")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status, body = e.code, json.loads(e.read())
        self.assertEqual(status, 415)

    def test_reassign_project_endpoint(self):
        status, body = self.request_json(
            "POST", "/tickets",
            {"ticket_type": "Bug", "reporter": "me", "actor": "agent", "summary": "placeholder"},
        )
        self.assertEqual(status, 201)
        tid = body["ticket_id"]

        status, body = self.request_json("POST", "/projects", {"codename": "TARGET", "prefix": "TGT"})
        self.assertEqual(status, 201)

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/reassign-project", {"actor": "agent", "project": "TGT"}
        )
        self.assertEqual(status, 200)
        new_tid = body["ticket_id"]
        self.assertTrue(new_tid.startswith("TGT-"))

        status, body = self.request_json("GET", f"/tickets/{new_tid}")
        self.assertEqual(status, 200)
        self.assertEqual(body["project_prefix"], "TGT")
        self.assertEqual(body["summary"], "placeholder")

        status, body = self.request_json("GET", f"/tickets/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "closed")

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/reassign-project", {"actor": "agent", "project": "no-such-prefix"}
        )
        self.assertEqual(status, 400)

        status, body = self.request_json(
            "POST", "/tickets/nonexistent/reassign-project", {"actor": "agent", "project": "TGT"}
        )
        self.assertEqual(status, 404)

    def test_priority_and_severity_endpoints(self):
        # The HTTP surface gets the setter too, because the reviewui and anything
        # else speaking to the server would otherwise still have only the diverting
        # /fields route to reach for.
        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Bug", "reporter": "me", "actor": "agent"},
        )
        tid = body["ticket_id"]

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/priority", {"actor": "agent", "value": 1})
        self.assertEqual(status, 200)
        status, body = self.request_json(
            "POST", f"/tickets/{tid}/severity", {"actor": "agent", "value": 0})
        self.assertEqual(status, 200)

        status, body = self.request_json("GET", f"/tickets/{tid}")
        self.assertEqual(body["priority"], 1)
        self.assertEqual(body["severity"], 0)
        self.assertEqual(body["custom_fields"], {})

        # An explicit null clears; an absent key is a caller mistake, not a clear.
        status, body = self.request_json(
            "POST", f"/tickets/{tid}/priority", {"actor": "agent", "value": None})
        self.assertEqual(status, 200)
        self.assertIsNone(self.request_json("GET", f"/tickets/{tid}")[1]["priority"])

        status, body = self.request_json("POST", f"/tickets/{tid}/priority", {"actor": "agent"})
        self.assertEqual(status, 400)
        self.assertIn("value", body["error"])

        status, body = self.request_json(
            "POST", f"/tickets/{tid}/priority", {"actor": "agent", "value": 9})
        self.assertEqual(status, 400)

        status, body = self.request_json(
            "POST", "/tickets/nonexistent/priority", {"actor": "agent", "value": 1})
        self.assertEqual(status, 404)

    def test_fields_endpoint_refuses_a_first_class_column(self):
        status, body = self.request_json(
            "POST", "/tickets", {"ticket_type": "Bug", "reporter": "me", "actor": "agent"},
        )
        tid = body["ticket_id"]
        status, body = self.request_json(
            "POST", f"/tickets/{tid}/fields",
            {"actor": "agent", "field_name": "priority", "field_value": "1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("priority", body["error"])
        after = self.request_json("GET", f"/tickets/{tid}")[1]
        self.assertEqual(after["custom_fields"], {})
        self.assertIsNone(after["priority"])


if __name__ == "__main__":
    unittest.main()
