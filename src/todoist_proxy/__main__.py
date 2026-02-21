from __future__ import annotations

import argparse
import socketserver
from wsgiref.simple_server import WSGIServer, make_server

from todoist_proxy.app import app


class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(prog="todoist-proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    with make_server(args.host, args.port, app, server_class=ThreadingWSGIServer) as server:
        print(f"todoist proxy listening on http://{args.host}:{args.port}")
        server.serve_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
