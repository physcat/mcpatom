"""mcpatom: a minimal MCP server in one stdlib-only module."""

import contextlib
import inspect
import json
import sys
from collections.abc import Callable
from typing import get_type_hints

PROTOCOL_VERSIONS = frozenset({"2025-06-18", "2025-11-25"})
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_BASIC_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

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


def _type_schema(annotation) -> dict:
    if annotation not in _BASIC_TYPES:  # fail registration rather than publish a wrong schema
        raise TypeError(f"unsupported annotation: {annotation}")
    return {"type": _BASIC_TYPES[annotation]}


def _input_schema(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    properties = {}
    required = []

    for name, param in sig.parameters.items():
        if name not in hints:
            raise TypeError(f"{getattr(fn, '__name__', fn)}: parameter '{name}' needs a type annotation")
        try:
            properties[name] = _type_schema(hints[name])
        except TypeError as e:
            raise TypeError(f"{getattr(fn, '__name__', fn)}: parameter '{name}': {e}") from None
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _name_and_description(fn: Callable, name: str | None, description: str | None) -> tuple[str, str]:
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
        raise TypeError(f"{getattr(fn, '__name__', fn)}: async def is unsupported; use asyncio.run(...) in a plain def")
    name = name or getattr(fn, "__name__", None)
    if name is None:
        raise TypeError(f"{fn!r} has no __name__ (functools.partial?); pass name=")
    if description is None:
        # getdoc on a partial or instance returns its class docstring: worse than empty.
        description = (inspect.getdoc(fn) or "") if hasattr(fn, "__name__") else ""
    return name, description


class Server:
    def __init__(self, name: str, *, version: str = "0.0.0", instructions: str | None = None):
        self.name = name
        self.version = version
        self.instructions = instructions
        self._tools: dict[str, tuple[Callable, dict]] = {}  # name -> (fn, wire-format listing entry)
        self._handlers = {
            "initialize": self._initialize,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "ping": self._ping,
        }

    def tool(self, fn: Callable | None = None, *, name: str | None = None, description: str | None = None):
        """Register a function as a tool, using its name, docstring and type
        annotations; optional name= and description= override."""
        if fn is not None and not callable(fn):
            raise TypeError(f"tool() takes no positional name; use @srv.tool(name='{fn}')")

        def register(fn: Callable) -> Callable:
            tool_name, desc = _name_and_description(fn, name, description)
            if tool_name in self._tools:
                raise ValueError(f"duplicate tool: {tool_name}")
            self._tools[tool_name] = (fn, {"name": tool_name, "description": desc, "inputSchema": _input_schema(fn)})
            return fn

        return register(fn) if fn is not None else register

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
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions is not None:
            result["instructions"] = self.instructions
        return result

    def _tools_list(self, _params: dict) -> dict:
        return {"tools": [entry for _, entry in self._tools.values()]}

    def _tools_call(self, params: dict) -> dict:
        """Any exception past the name lookup is an execution error the model
        should see and self-correct"""
        name = params.get("name")
        if name not in self._tools:
            raise ValueError(f"unknown tool: {name}")

        fn, _ = self._tools[name]
        try:
            result = fn(**(params.get("arguments") or {}))
            if result is None:
                return {"content": []}
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}

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
