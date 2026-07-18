import io
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Optional, TypedDict

import pytest

import mcpatom
from mcpatom import Server

if TYPE_CHECKING:

    class OnlyAtCheckTime: ...


def srv(**kwargs):
    return Server("test-server", version="0.0.1", **kwargs)


def request(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def srv_tools(s):
    return s.handle_message(request("tools/list"))["result"]["tools"]


def test_initialize():
    s = srv(instructions="Search before you modify.")

    @s.resource("app://doc")
    def doc() -> str:
        """Doc."""
        return ""

    for version in mcpatom.PROTOCOL_VERSIONS:
        resp = s.handle_message(request("initialize", {"protocolVersion": version}, id=7))
        assert resp == {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}, "resources": {}},  # prompts absent: none registered
                "serverInfo": {"name": "test-server", "version": "0.0.1"},
                "instructions": "Search before you modify.",
            },
        }

    # An unknown version gets a counter-offer, never a rejection.
    resp = srv().handle_message(request("initialize", {"protocolVersion": "2024-11-05"}))
    assert resp["result"]["protocolVersion"] == "2025-06-18"


def test_schema_generation():
    s = srv()
    marker = object()  # non-str Annotated metadata is someone else's protocol: ignored

    @s.tool
    def echo(
        a: Annotated[str, "Search query syntax."],
        _b: Annotated[int, marker],
        _c: float,
        _d: bool,
        _e: list[str],
        _f: Literal["x", "y"],
        _g: str | None,
        _h: list,
        _i: Optional[Literal["x", "y"]] = None,  # noqa: UP045 (typing.Union origin, deliberately distinct from _g)
        _j: Annotated[Literal["name", "date"], marker, "Sort order."] | None = None,
    ) -> str:
        """Echo things back."""
        return a

    [t] = srv_tools(s)
    assert t == {
        "name": "echo",
        "description": "Echo things back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "Search query syntax."},
                "_b": {"type": "integer"},
                "_c": {"type": "number"},
                "_d": {"type": "boolean"},
                "_e": {"type": "array", "items": {"type": "string"}},
                "_f": {"enum": ["x", "y"]},
                "_g": {"type": ["string", "null"]},
                "_h": {"type": "array"},
                "_i": {"enum": ["x", "y", None]},
                "_j": {"enum": ["name", "date", None], "description": "Sort order."},
            },
            "required": ["a", "_b", "_c", "_d", "_e", "_f", "_g", "_h"],
        },
    }


def test_typeddict_schemas():
    s = srv()

    class Query(TypedDict, total=False):
        tag: str

    class Report(TypedDict):
        count: int
        label: Annotated[str, "Display label."]

    @s.tool
    def report(query: Query) -> Report:
        """Report."""
        return {"count": 3, "label": "x"}

    [tool] = srv_tools(s)
    # total=False means no required keys.
    assert tool["inputSchema"]["properties"]["query"] == {
        "type": "object",
        "properties": {"tag": {"type": "string"}},
    }
    # A TypedDict return annotation publishes the matching outputSchema.
    assert tool["outputSchema"] == {
        "type": "object",
        "properties": {"count": {"type": "integer"}, "label": {"type": "string", "description": "Display label."}},
        "required": ["count", "label"],
    }


def test_explicit_schemas_bypass_generation():
    s = srv()
    out = {"type": "object", "properties": {"n": {"type": "integer"}}}

    @s.tool(input_schema={}, output_schema=out)
    def opaque(**kwargs) -> str:
        """Takes anything."""
        return str(kwargs)

    # The bypass must be complete: this annotation cannot evaluate at runtime.
    @s.tool(input_schema={"type": "object"})
    def exotic(x: "OnlyAtCheckTime") -> str:
        """Signature the generator cannot express."""
        return ""

    tools = {t["name"]: t for t in srv_tools(s)}
    assert tools["opaque"]["inputSchema"] == {}  # falsy but legal: must not fall back to generation
    assert tools["opaque"]["outputSchema"] == out
    assert tools["exotic"]["inputSchema"] == {"type": "object"}
    assert "outputSchema" not in tools["exotic"]


def test_registration_failures():
    s = srv()

    # The likeliest first-session error names the function and the parameter.
    with pytest.raises(TypeError, match=r"bad_type: parameter 'x': unsupported annotation"):

        @s.tool
        def bad_type(x: dict) -> str:
            """Unsupported param type."""
            return str(x)

    with pytest.raises(TypeError, match="needs a type annotation"):

        @s.tool
        def unannotated(x) -> str:
            """Missing annotation."""
            return str(x)

    with pytest.raises(TypeError, match="async def is unsupported"):

        @s.tool
        async def slow() -> str:
            """Async tools can never work in a sync server."""
            return ""

    with pytest.raises(TypeError):

        @s.tool(extra={"icon": object()})  # would otherwise poison every tools/list response
        def decorated() -> str:
            """Decorated."""
            return ""

    @s.tool
    def once() -> str:
        """First."""
        return ""

    with pytest.raises(ValueError, match="duplicate tool"):
        s.tool(once)


def test_tools_call_result_shapes():
    s = srv()

    @s.tool
    def shout(word: str) -> str:
        """Shout."""
        return word.upper()

    @s.tool
    def quiet() -> None:
        """Returns nothing."""

    @s.tool
    def hello() -> dict:
        """Hello."""
        return {"hello": "こんにちは"}

    @s.tool
    def items() -> list:
        """Items."""
        return ["a", "b"]

    resp = s.handle_message(request("tools/call", {"name": "shout", "arguments": {"word": "hi"}}))
    assert resp["result"] == {"content": [{"type": "text", "text": "HI"}]}

    resp = s.handle_message(request("tools/call", {"name": "quiet"}))  # arguments omitted: no-arg call
    assert resp["result"] == {"content": []}  # None is no content, not the text "null"

    resp = s.handle_message(request("tools/call", {"name": "hello"}))
    assert resp["result"] == {
        "content": [{"type": "text", "text": '{"hello": "こんにちは"}'}],
        "structuredContent": {"hello": "こんにちは"},
    }

    resp = s.handle_message(request("tools/call", {"name": "items"}))
    assert "structuredContent" not in resp["result"]  # must be a JSON object; lists stay text-only

    # Recorded decision: any exception from the call, this binding failure
    # included, is isError content, never JSON-RPC -32602.
    resp = s.handle_message(request("tools/call", {"name": "shout", "arguments": {"wrong": 1}}))
    assert "error" not in resp
    assert resp["result"]["isError"] is True
    [block] = resp["result"]["content"]
    assert block["type"] == "text" and block["text"].startswith("TypeError: ")

    # Only an unknown tool name is a protocol-level fault.
    resp = s.handle_message(request("tools/call", {"name": "nope", "arguments": {}}))
    assert resp["error"]["code"] == mcpatom.INVALID_PARAMS


def test_tool_tuple_returns_multiple_blocks():
    s = srv()
    link = {"type": "resource_link", "uri": "app://x", "name": "x"}

    @s.tool
    def screenshot():
        """Screenshot with caption and a link."""
        return mcpatom.Image(b"\x89PNG", "image/png"), "viewport 800x600", link, {"n": 1}, {"type": "sedan"}

    resp = s.handle_message(request("tools/call", {"name": "screenshot"}))
    assert resp["result"] == {
        "content": [
            {"type": "image", "data": "iVBORw==", "mimeType": "image/png"},
            {"type": "text", "text": "viewport 800x600"},
            link,  # a dict whose "type" names a spec block type passes through verbatim
            {"type": "text", "text": '{"n": 1}'},  # one without stays data
            {"type": "text", "text": '{"type": "sedan"}'},  # ambient "type" keys must not enter the block union
        ]
    }


def test_unserialisable_results_cannot_poison_the_transport():
    # Unserialisable results must fail inside the handler's isError net,
    # never at the transport's json.dumps, which would kill a stdio server.
    s = srv()

    @s.tool
    def bad_result():
        """Unserialisable plain result."""
        return {"when": object()}

    @s.tool
    def bad_block():
        """Verbatim block smuggling bytes."""
        return ({"type": "text", "text": b"raw"},)

    @s.prompt
    def bad_messages():
        """Messages smuggling bytes."""
        return [{"role": "user", "content": {"type": "text", "text": b"raw"}}]

    for name in ("bad_result", "bad_block"):
        resp = s.handle_message(request("tools/call", {"name": name}))
        assert "error" not in resp
        assert resp["result"]["isError"] is True
        json.dumps(resp)  # the response itself stays serialisable
    resp = s.handle_message(request("prompts/get", {"name": "bad_messages"}))
    assert resp["error"]["code"] == mcpatom.INVALID_PARAMS
    json.dumps(resp)


def test_resources():
    s = srv()

    @s.resource("app://search-syntax", mime_type="text/markdown")
    def search_syntax() -> str:
        """Search syntax."""
        return "# Searching"

    @s.resource("app://logo", mime_type="image/png")
    def logo() -> bytes:
        """Logo."""
        return b"\x89PNG"

    resp = s.handle_message(request("resources/read", {"uri": "app://search-syntax"}))
    assert resp["result"] == {
        "contents": [{"uri": "app://search-syntax", "mimeType": "text/markdown", "text": "# Searching"}]
    }

    # bytes returns become a base64 blob, not text.
    resp = s.handle_message(request("resources/read", {"uri": "app://logo"}))
    assert resp["result"] == {"contents": [{"uri": "app://logo", "mimeType": "image/png", "blob": "iVBORw=="}]}

    # Unlike tools/call, read failures are JSON-RPC errors (per spec).
    resp = s.handle_message(request("resources/read", {"uri": "app://nope"}))
    assert resp["error"]["code"] == mcpatom.RESOURCE_NOT_FOUND


def test_prompts():
    s = srv()

    @s.prompt
    def summarise(topic: Annotated[str, "Topic to cover."], length: str = "short") -> str:
        """Summarise a topic."""
        return f"Write a {length} summary of {topic}."

    fewshot_messages = [
        {"role": "user", "content": {"type": "text", "text": "Example in."}},
        {"role": "assistant", "content": {"type": "text", "text": "Example out."}},
    ]

    @s.prompt
    def fewshot():
        """Few-shot."""
        return fewshot_messages

    [entry, _] = s.handle_message(request("prompts/list"))["result"]["prompts"]
    assert entry["arguments"] == [
        {"name": "topic", "description": "Topic to cover.", "required": True},
        {"name": "length"},
    ]

    resp = s.handle_message(request("prompts/get", {"name": "summarise", "arguments": {"topic": "tea"}}))
    assert resp["result"]["messages"] == [
        {"role": "user", "content": {"type": "text", "text": "Write a short summary of tea."}}
    ]

    # A list return is pre-built message dicts, passed through verbatim.
    resp = s.handle_message(request("prompts/get", {"name": "fewshot"}))
    assert resp["result"]["messages"] == fewshot_messages

    resp = s.handle_message(request("prompts/get", {"name": "nope"}))
    assert resp["error"]["code"] == mcpatom.INVALID_PARAMS

    # Prompt argument values are strings on the wire: any other annotation
    # is a registration error.
    with pytest.raises(TypeError, match="must be annotated str"):
        s.prompt(lambda count=3: "x")


def test_stdio_session_and_recovery():
    out = io.StringIO()
    lines = (
        '{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}\n'
        '{"jsonrpc": "2.0", "method": "notifications/initialized"}\n'
        "\n"
        "{nope\n"
        "[1, 2]\n"
        '{"jsonrpc": "2.0", "id": 5, "method": "ping"}\n'
    )
    srv().serve_stdio(io.StringIO(lines), out)
    responses = [json.loads(line) for line in out.getvalue().splitlines()]  # one line per response, by construction
    # The notification and blank line produce nothing; framing faults answer
    # with id null (the request's own id is unrecoverable) and serving continues.
    assert [r.get("id") for r in responses] == [0, None, None, 5]
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert responses[1]["error"]["code"] == mcpatom.PARSE_ERROR
    assert responses[2]["error"]["code"] == mcpatom.INVALID_REQUEST
    assert responses[3]["result"] == {}


def test_stdio_redirects_print_to_stderr():
    # Over the real streams only: stray print() must land in stderr (client
    # logs), not corrupt the protocol on stdout.
    script = (
        f"import sys; sys.path.insert(0, {str(Path(mcpatom.__file__).parent)!r})\n"
        "from mcpatom import Server\n"
        "srv = Server('noisy-server')\n"
        "@srv.tool\n"
        "def noisy() -> str:\n"
        "    'Noisy.'\n"
        "    print('debug chatter')\n"
        "    return 'ok'\n"
        "srv.serve_stdio()\n"
    )
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "noisy"}}
    proc = subprocess.run(
        [sys.executable, "-c", script], input=json.dumps(call), capture_output=True, text=True, timeout=30
    )
    [response] = [json.loads(line) for line in proc.stdout.splitlines()]
    assert response["result"]["content"] == [{"type": "text", "text": "ok"}]
    assert "debug chatter" in proc.stderr
