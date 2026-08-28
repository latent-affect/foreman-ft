import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from ...api.http_api import build_server
from ...gitops.gitops import GitOps
from ...store.store import Store


class HeaderTests(unittest.TestCase):
    def test_csp_header_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "test.db", codename="TESTPROJ", prefix="TP")
            gitops = GitOps(store, root / "stages")
            docs_root = root / "docs"
            docs_root.mkdir()
            server = build_server(store, gitops, docs_root)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                    csp = resp.headers.get("Content-Security-Policy")
                    self.assertIsNotNone(csp)
                    self.assertIn("script-src 'self'", csp)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
