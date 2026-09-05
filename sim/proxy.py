#!/usr/bin/env python3
"""Local streaming pass-through proxy with a file-controlled dead state."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import SplitResult, urlsplit


STATE_DIR = Path(
    os.environ.get("WATCHER_STATE_DIR", Path.home() / ".immortal-agents")
)
DEAD_PATH = STATE_DIR / "proxy_dead.json"
DEAD_BODY = (
    b"unexpected status 502 Bad Gateway: Provider unreachable: Unable to connect. "
    b"Is the computer able to access the url?"
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
STREAM_CHUNK_SIZE = 64 * 1024
DEAD_POLL_SECONDS = 0.05


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry


def read_dead_state(path: Path) -> dict[str, object] | None:
    """Return an active dead state, deleting it after expiry."""
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    expiry = _parse_expiry(state.get("expires_at"))
    if expiry is None:
        return None
    if expiry <= datetime.now(timezone.utc):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Expiry still restores service if the process cannot clean up the file.
            pass
        return None
    return state


def _upstream_target(upstream: SplitResult, request_target: str) -> str:
    incoming = urlsplit(request_target)
    base_path = upstream.path.rstrip("/")
    incoming_path = incoming.path if incoming.path.startswith("/") else f"/{incoming.path}"
    path = f"{base_path}{incoming_path}" or "/"
    queries = [query for query in (upstream.query, incoming.query) if query]
    return f"{path}?{'&'.join(queries)}" if queries else path


def _host_header(upstream: SplitResult) -> str:
    hostname = upstream.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{host}:{upstream.port}" if upstream.port is not None else host


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        upstream: str,
        dead_mode: str,
        dead_path: Path = DEAD_PATH,
    ) -> None:
        parsed = urlsplit(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("--upstream must be an http or https URL")
        if dead_mode not in {"502", "drop"}:
            raise ValueError("--dead-mode must be 502 or drop")
        self.upstream = parsed
        self.dead_mode = dead_mode
        self.dead_path = Path(dead_path)
        self._active_sockets: set[socket.socket] = set()
        self._active_lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._was_dead = self.is_dead()
        super().__init__(server_address, ProxyHandler)
        self._monitor = threading.Thread(
            target=self._monitor_dead_state,
            name="proxy-dead-state",
            daemon=True,
        )
        self._monitor.start()

    def is_dead(self) -> bool:
        return read_dead_state(self.dead_path) is not None

    def register_socket(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        with self._active_lock:
            self._active_sockets.add(sock)

    def unregister_socket(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        with self._active_lock:
            self._active_sockets.discard(sock)

    def _close_active_sockets(self) -> None:
        with self._active_lock:
            sockets = list(self._active_sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _monitor_dead_state(self) -> None:
        while not self._monitor_stop.wait(DEAD_POLL_SECONDS):
            dead = self.is_dead()
            if dead and not self._was_dead:
                self._close_active_sockets()
            self._was_dead = dead

    def server_close(self) -> None:
        self._monitor_stop.set()
        self._close_active_sockets()
        monitor = getattr(self, "_monitor", None)
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=1)
        super().server_close()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ProxyServer

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._handle_request
        raise AttributeError(name)

    def _read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            chunks: list[bytes] = []
            while True:
                size_line = self.rfile.readline()
                if not size_line:
                    raise ConnectionError("request ended inside chunked body")
                size = int(size_line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline() not in {b"\r\n", b"\n", b""}:
                        pass
                    return b"".join(chunks)
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
        length = self.headers.get("Content-Length")
        return self.rfile.read(int(length)) if length else b""

    def _request_headers(self, body: bytes) -> list[tuple[str, str]]:
        connection_tokens = {
            token.strip().lower()
            for token in self.headers.get("Connection", "").split(",")
            if token.strip()
        }
        excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"host"}
        headers = [
            (name, value)
            for name, value in self.headers.items()
            if name.lower() not in excluded
        ]
        headers.append(("Host", _host_header(self.server.upstream)))
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            headers = [
                (name, value) for name, value in headers if name.lower() != "content-length"
            ]
            headers.append(("Content-Length", str(len(body))))
        return headers

    def _new_upstream_connection(self) -> http.client.HTTPConnection:
        connection_class = (
            http.client.HTTPSConnection
            if self.server.upstream.scheme == "https"
            else http.client.HTTPConnection
        )
        return connection_class(
            self.server.upstream.hostname,
            self.server.upstream.port,
            timeout=30,
        )

    def _drop(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass

    def _dead_response(self) -> None:
        if self.server.dead_mode == "drop":
            self._drop()
            return
        self.send_response_only(502, "Bad Gateway")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(DEAD_BODY)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(DEAD_BODY)
        self.wfile.flush()
        self.close_connection = True

    def _send_upstream_error(self) -> None:
        body = b"Bad Gateway"
        try:
            self.send_response_only(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True

    def _handle_request(self) -> None:
        if self.server.is_dead():
            self._dead_response()
            return

        downstream = self.connection
        upstream_socket: socket.socket | None = None
        connection: http.client.HTTPConnection | None = None
        response_started = False
        self.server.register_socket(downstream)
        try:
            if self.server.is_dead():
                self._drop()
                return
            body = self._read_request_body()
            connection = self._new_upstream_connection()
            connection.putrequest(
                self.command,
                _upstream_target(self.server.upstream, self.path),
                skip_host=True,
                skip_accept_encoding=True,
            )
            for name, value in self._request_headers(body):
                connection.putheader(name, value)
            connection.endheaders(body if body else None)
            upstream_socket = connection.sock
            self.server.register_socket(upstream_socket)
            if self.server.is_dead():
                self._drop()
                return

            response = connection.getresponse()
            if connection.sock is not upstream_socket:
                self.server.unregister_socket(upstream_socket)
                upstream_socket = connection.sock
                self.server.register_socket(upstream_socket)
            if self.server.is_dead():
                self._drop()
                return

            self.send_response_only(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() in HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            self.close_connection = True

            while not self.server.is_dead():
                chunk = response.read1(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            if self.server.is_dead():
                self._drop()
        except (ConnectionError, OSError, ValueError, http.client.HTTPException):
            if self.server.is_dead() or response_started:
                self._drop()
            else:
                self._send_upstream_error()
        finally:
            self.server.unregister_socket(upstream_socket)
            self.server.unregister_socket(downstream)
            if connection is not None:
                connection.close()


def create_server(
    port: int,
    upstream: str,
    dead_mode: str,
    dead_path: Path = DEAD_PATH,
) -> ProxyServer:
    return ProxyServer(("127.0.0.1", port), upstream, dead_mode, dead_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the local proxy")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--upstream", required=True)
    serve.add_argument("--dead-mode", choices=("502", "drop"), default="502")
    args = parser.parse_args()

    server = create_server(args.port, args.upstream, args.dead_mode)
    print(
        f"proxy listening on http://127.0.0.1:{server.server_port} "
        f"-> {args.upstream} (dead mode: {args.dead_mode})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
