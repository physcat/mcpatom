"""mcpatom: a minimal MCP server in one stdlib-only module."""

import contextlib
import json
import sys

PROTOCOL_VERSIONS = frozenset({"2025-06-18", "2025-11-25"})
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _result(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _parse(raw) -> dict:
    try:
        msg = json.loads(raw)
    except ValueError:
        raise _JsonRpcError(PARSE_ERROR, "parse error") from None
    if not isinstance(msg, dict):
        raise _JsonRpcError(INVALID_REQUEST, "message must be a JSON object")
    return msg


class Server:
    def __init__(self, name: str, *, version: str = "0.0.0", instructions: str | None = None):
        self.name = name
        self.version = version
        self.instructions = instructions
        self._handlers = {
            "initialize": self._initialize,
            "ping": self._ping,
        }

    def handle_message(self, msg: dict) -> dict | None:
        """Assumes well-formed JSON-RPC from a real MCP client; malformed input
        gets a best-effort error response rather than field-by-field rejection.
        A message without an "id" is a notification and never gets a response."""

        id = msg.get("id")
        if id is None:
            return None

        try:
            handler = self._handlers.get(msg.get("method"))
            if handler is None:
                return _error(id, METHOD_NOT_FOUND, f"method not found: {msg.get('method')}")

            return _result(id, handler(msg.get("params") or {}))
        except (TypeError, ValueError) as e:
            return _error(id, INVALID_PARAMS, str(e))
        except Exception as e:
            return _error(id, INTERNAL_ERROR, str(e))

    def _initialize(self, params: dict) -> dict:
        # Echo the client's version if we support it, else counter-offer
        version = params.get("protocolVersion")
        result = {
            "protocolVersion": version if version in PROTOCOL_VERSIONS else _DEFAULT_PROTOCOL_VERSION,
            "capabilities": {},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions is not None:
            result["instructions"] = self.instructions
        return result

    def _ping(self, _params: dict) -> dict:
        return {}

    def serve_stdio(self, stdin=None, stdout=None):
        """Serve newline-delimited JSON-RPC until EOF.

        While serving the real stdout, where a stray print() would corrupt
        the protocol, sys.stdout is redirected to stderr"""
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        redirect = contextlib.redirect_stdout(sys.stderr) if stdout is sys.stdout else contextlib.nullcontext()
        try:
            with redirect:
                for line in stdin:
                    if not line.strip():
                        continue
                    try:
                        response = self.handle_message(_parse(line))
                    except _JsonRpcError as e:
                        response = _error(None, e.code, str(e))  # can't get ID when parsing fails.
                    if response is not None:
                        stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                        stdout.flush()
        except (BrokenPipeError, KeyboardInterrupt):
            pass
