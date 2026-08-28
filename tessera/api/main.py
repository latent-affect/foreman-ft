import argparse
import sys

from ..gitops.gitops import GitOps
from ..store.store import Store
from .http_api import build_server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tessera-server")
    parser.add_argument("--db", required=True)
    parser.add_argument("--stages-root", required=True)
    parser.add_argument("--docs-root", required=True)
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--codename")
    parser.add_argument("--prefix")
    args = parser.parse_args(argv)

    store = Store(args.db, codename=args.codename, prefix=args.prefix)
    gitops = GitOps(store, args.stages_root)
    server = build_server(store, gitops, args.docs_root, host="127.0.0.1", port=args.port)
    print(f"tessera server listening on http://127.0.0.1:{server.server_address[1]}")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
