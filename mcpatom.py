"""mcpatom: a minimal MCP server in one stdlib-only module."""

import base64
import contextlib
import inspect
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import NoneType, UnionType
from typing import Annotated, ClassVar, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

PROTOCOL_VERSIONS = frozenset({"2025-06-18", "2025-11-25"})
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_BASIC_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

RESOURCE_NOT_FOUND = -32002  # MCP-defined, not JSON-RPC


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
    if annotation in _BASIC_TYPES:
        return {"type": _BASIC_TYPES[annotation]}
    if annotation is list:
        return {"type": "array"}
    if is_typeddict(annotation):
        hints = get_type_hints(annotation, include_extras=True)
        schema: dict = {"type": "object", "properties": {k: _type_schema(v) for k, v in hints.items()}}
        required = [k for k in hints if k in annotation.__required_keys__]
        if required:
            schema["required"] = required
        return schema

    origin = get_origin(annotation)
    if origin is Annotated:
        inner, *extras = get_args(annotation)
        # non-str metadata is someone else's protocol; ignore it, as checkers do.
        description = next((e for e in extras if isinstance(e, str)), None)
        return _type_schema(inner) if description is None else {**_type_schema(inner), "description": description}
    if origin is list:
        return {"type": "array", "items": _type_schema(get_args(annotation)[0])}
    if origin is Literal:
        return {"enum": list(get_args(annotation))}
    if origin in (Union, UnionType):  # typing.Optional[X] and X | None respectively
        args = get_args(annotation)
        if len(args) != 2 or NoneType not in args:
            raise TypeError(f"unsupported union (only X | None): {annotation}")
        schema = _type_schema(args[0] if args[1] is NoneType else args[1])
        if "enum" in schema:
            schema["enum"] = [*schema["enum"], None]
        else:
            schema["type"] = [schema["type"], "null"]
        return schema

    # fail registration rather than publish a wrong schema
    raise TypeError(
        f"unsupported annotation: {annotation}"
        " (supported: str, int, float, bool, list[X], Literal[...], TypedDict, X | None;"
        " a hand-written input_schema= bypasses generation)"
    )


def _input_schema(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
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


def _output_schema(fn: Callable) -> dict | None:
    try:
        annotation = get_type_hints(fn, include_extras=True).get("return")
    except Exception:  # unevaluable annotation (TYPE_CHECKING-only names): no outputSchema, not an error
        return None
    # only a TypedDict can describe structuredContent (a JSON object)
    return _type_schema(annotation) if is_typeddict(annotation) else None


def _prompt_arguments(fn: Callable) -> list[dict]:
    """Prompt argument values are always strings on the wire, so every parameter
    must be annotated str; a default makes it optional."""
    arguments = []
    hints = get_type_hints(fn, include_extras=True)
    for name, param in inspect.signature(fn).parameters.items():
        annotation, description = hints.get(name), None
        if get_origin(annotation) is Annotated:
            annotation, *extras = get_args(annotation)
            description = next((e for e in extras if isinstance(e, str)), None)
        if annotation is not str:
            raise TypeError(
                f"{getattr(fn, '__name__', fn)}: prompt argument '{name}' must be annotated str"
                " (prompt arguments are strings on the wire)"
            )
        arguments.append(
            {"name": name}
            | ({"description": description} if description is not None else {})
            | ({"required": True} if param.default is inspect.Parameter.empty else {})
        )
    return arguments


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


def _register(registry: dict, kind: str, key: str, fn: Callable, entry: dict) -> None:
    if key in registry:
        raise ValueError(f"duplicate {kind}: {key}")
    # a bad input_schema or extra block must fail at registration, using dumps to test it
    json.dumps(entry, allow_nan=False)  # NaN/Infinity aren't JSON
    registry[key] = (fn, entry)


@dataclass(frozen=True)
class _Media:
    """Return Image(data, mime_type) or Audio(...) from a tool to emit one
    binary content block; data is raw bytes, base64-encoded on the wire."""

    data: bytes
    mime_type: str

    _TYPE: ClassVar[str]

    def _block(self) -> dict:
        return {"type": self._TYPE, "data": base64.b64encode(self.data).decode(), "mimeType": self.mime_type}


class Image(_Media):
    _TYPE = "image"


class Audio(_Media):
    _TYPE = "audio"


_BLOCK_TYPES = frozenset({"text", "image", "audio", "resource_link", "resource"})


def _content_block(item) -> dict:
    if isinstance(item, _Media):
        return item._block()
    # a dict whose "type" names a spec block type passes through verbatim;
    # ambient "type" keys in ordinary data must not enter the block union.
    if isinstance(item, dict) and item.get("type") in _BLOCK_TYPES:
        return item
    return {"type": "text", "text": item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}


class Server:
    def __init__(self, name: str, *, version: str = "0.0.0", instructions: str | None = None):
        self.name = name
        self.version = version
        self.instructions = instructions
        # each registry maps name/uri -> (fn, wire-format listing entry)
        self._tools: dict[str, tuple[Callable, dict]] = {}
        self._resources: dict[str, tuple[Callable, dict]] = {}
        self._prompts: dict[str, tuple[Callable, dict]] = {}
        self._handlers = {
            "initialize": self._initialize,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": self._prompts_list,
            "prompts/get": self._prompts_get,
            "ping": self._ping,
        }

    def tool(
        self,
        fn: Callable | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        extra: dict | None = None,
    ):
        """Register a function as a tool, using its name, docstring and type
        annotations; optional name= and description= override, and input_schema=
        and output_schema= bypass generation entirely. extra= merges raw fields
        (title, annotations, ...) into the tools/list entry verbatim."""
        if fn is not None and not callable(fn):
            raise TypeError(f"tool() takes no positional name; use @srv.tool(name='{fn}')")

        def register(fn: Callable) -> Callable:
            tool_name, desc = _name_and_description(fn, name, description)
            schema = _input_schema(fn) if input_schema is None else input_schema
            entry = {"name": tool_name, "description": desc, "inputSchema": schema}
            if (out := _output_schema(fn) if output_schema is None else output_schema) is not None:
                entry["outputSchema"] = out
            _register(self._tools, "tool", tool_name, fn, entry | (extra or {}))
            return fn

        return register(fn) if fn is not None else register

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str = "text/plain",
        extra: dict | None = None,
    ):
        """Register a function returning str (text) or bytes (base64 blob) as a
        readable resource, run on every resources/read; optional name=,
        description= and extra= as in tool()."""
        if not isinstance(uri, str):  # bare @srv.resource would otherwise silently register nothing
            raise TypeError("resource() needs a uri string: @srv.resource('scheme://path')")

        def register(fn: Callable) -> Callable:
            resource_name, desc = _name_and_description(fn, name, description)
            entry = {"uri": uri, "name": resource_name, "description": desc, "mimeType": mime_type}
            _register(self._resources, "resource", uri, fn, entry | (extra or {}))
            return fn

        return register

    def prompt(
        self,
        fn: Callable | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        extra: dict | None = None,
    ):
        """Register a function as a prompt; optional name=, description= and
        extra= as in tool(). Parameters must be annotated str, and it returns a
        str for a single user message or a list of message dicts passed through
        verbatim."""
        if fn is not None and not callable(fn):
            raise TypeError(f"prompt() takes no positional name; use @srv.prompt(name='{fn}')")

        def register(fn: Callable) -> Callable:
            prompt_name, desc = _name_and_description(fn, name, description)
            entry = {"name": prompt_name, "description": desc}
            if args := _prompt_arguments(fn):
                entry["arguments"] = args
            _register(self._prompts, "prompt", prompt_name, fn, entry | (extra or {}))
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
        except _JsonRpcError as e:
            return _error(id, e.code, str(e))
        except (TypeError, ValueError) as e:
            return _error(id, INVALID_PARAMS, str(e))
        except Exception as e:
            return _error(id, INTERNAL_ERROR, str(e))

    def _initialize(self, params: dict) -> dict:
        # Echo the client's version if we support it, else counter-offer
        version = params.get("protocolVersion")
        result = {
            "protocolVersion": version if version in PROTOCOL_VERSIONS else _DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {}}
            | ({"resources": {}} if self._resources else {})
            | ({"prompts": {}} if self._prompts else {}),
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions is not None:
            result["instructions"] = self.instructions
        return result

    def _tools_list(self, _params: dict) -> dict:
        return {"tools": [entry for _, entry in self._tools.values()]}

    def _resources_list(self, _params: dict) -> dict:
        return {"resources": [entry for _, entry in self._resources.values()]}

    def _resources_read(self, params: dict) -> dict:
        """Unlike tools/call, read failures are JSON-RPC errors (per spec):
        TypeError/ValueError from the function surface as -32602, others -32603."""
        uri = params.get("uri")
        if uri not in self._resources:
            raise _JsonRpcError(RESOURCE_NOT_FOUND, f"resource not found: {uri}")

        fn, entry = self._resources[uri]
        result = fn()
        body = (
            {"blob": base64.b64encode(result).decode()}
            if isinstance(result, bytes)
            else {"text": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)}
        )
        return {"contents": [{"uri": uri, "mimeType": entry["mimeType"], **body}]}

    def _prompts_list(self, _params: dict) -> dict:
        return {"prompts": [entry for _, entry in self._prompts.values()]}

    def _prompts_get(self, params: dict) -> dict:
        """As with resources, failures are JSON-RPC errors: unknown name and bad
        arguments are -32602 (per spec), anything else -32603."""
        name = params.get("name")
        if name not in self._prompts:
            raise ValueError(f"unknown prompt: {name}")

        fn, entry = self._prompts[name]
        result = fn(**(params.get("arguments") or {}))
        if isinstance(result, list):  # pre-built message dicts, e.g. few-shot pairs
            json.dumps(result)  # unserialisable messages must fail here as a JSON-RPC error, not at the transport
            return {"description": entry["description"], "messages": result}
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return {
            "description": entry["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }

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
            if isinstance(result, dict):
                # structuredContent must be a JSON object, so lists/scalars stay text-only.
                return {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "structuredContent": result,
                }
            items = result if isinstance(result, tuple) else (result,)  # a tuple is several blocks at once
            content = [_content_block(item) for item in items]
            json.dumps(content)  # verbatim blocks must fail inside the isError net, not at the transport
            return {"content": content}
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
