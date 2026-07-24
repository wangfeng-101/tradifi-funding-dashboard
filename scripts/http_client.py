from __future__ import annotations

import gzip
import http.client
import json
import ssl
import threading
import urllib.parse
from typing import Any


class JsonRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


def decode_body(payload: bytes, content_encoding: str) -> bytes:
    encodings = {
        value.strip().lower()
        for value in content_encoding.split(",")
        if value.strip()
    }
    if "gzip" in encodings:
        return gzip.decompress(payload)
    return payload


class JsonHttpClient:
    """Small thread-local HTTP/1.1 pool for public JSON APIs."""

    def __init__(
        self,
        *,
        timeout: float = 30,
        user_agent: str = "Mozilla/5.0",
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self._local = threading.local()

    def _connections(
        self,
    ) -> dict[tuple[str, str, int | None], http.client.HTTPConnection]:
        connections = getattr(self._local, "connections", None)
        if connections is None:
            connections = {}
            self._local.connections = connections
        return connections

    def _connection(
        self,
        parsed: urllib.parse.SplitResult,
    ) -> tuple[
        tuple[str, str, int | None],
        http.client.HTTPConnection,
    ]:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise JsonRequestError(f"unsupported URL: {parsed.geturl()}")

        key = (parsed.scheme, parsed.hostname, parsed.port)
        connections = self._connections()
        connection = connections.get(key)
        if connection is None:
            if parsed.scheme == "https":
                connection = http.client.HTTPSConnection(
                    parsed.hostname,
                    parsed.port,
                    timeout=self.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                connection = http.client.HTTPConnection(
                    parsed.hostname,
                    parsed.port,
                    timeout=self.timeout,
                )
            connections[key] = connection
        return key, connection

    def _discard(
        self,
        key: tuple[str, str, int | None],
        connection: http.client.HTTPConnection,
    ) -> None:
        try:
            connection.close()
        finally:
            self._connections().pop(key, None)

    def request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        parsed = urllib.parse.urlsplit(url)
        query = parsed.query
        if params:
            encoded = urllib.parse.urlencode(params)
            query = f"{query}&{encoded}" if query else encoded
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", query, ""))

        request_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self.user_agent,
        }
        if headers:
            request_headers.update(headers)

        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        key, connection = self._connection(parsed)
        try:
            connection.request(
                method,
                path,
                body=payload,
                headers=request_headers,
            )
            response = connection.getresponse()
            raw = response.read()
            response_headers = {
                name.lower(): value for name, value in response.getheaders()
            }
            status = response.status
            will_close = response.will_close
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            self._discard(key, connection)
            raise JsonRequestError(f"request failed for {url}: {exc}") from exc

        if will_close:
            self._discard(key, connection)

        try:
            decoded = decode_body(raw, response_headers.get("content-encoding", ""))
        except (OSError, EOFError) as exc:
            raise JsonRequestError(
                f"invalid compressed response from {url}: {exc}",
                status=status,
                headers=response_headers,
            ) from exc

        if not 200 <= status < 300:
            message = decoded.decode("utf-8", errors="replace")[:300]
            raise JsonRequestError(
                f"HTTP {status} from {url}: {message}",
                status=status,
                headers=response_headers,
            )

        try:
            return json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JsonRequestError(
                f"invalid JSON response from {url}: {exc}",
                status=status,
                headers=response_headers,
            ) from exc
