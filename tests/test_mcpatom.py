import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import mcpatom
from mcpatom import Server


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
    for version in mcpatom.PROTOCOL_VERSIONS:
        resp = s.handle_message(request("initialize", {"protocolVersion": version}, id=7))
        assert resp == {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-server", "version": "0.0.1"},
                "instructions": "Search before you modify.",
            },
        }

    # An unknown version gets a counter-offer, never a rejection.
    resp = srv().handle_message(request("initialize", {"protocolVersion": "2024-11-05"}))
    assert resp["result"]["protocolVersion"] == "2025-06-18"


def test_schema_generation():
    s = srv()

    @s.tool
    def echo(a: str, _b: int, _c: float, _d: bool, _e: str = "x") -> str:
        """Echo things back."""
        return a

    [t] = srv_tools(s)
    assert t == {
        "name": "echo",
        "description": "Echo things back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "_b": {"type": "integer"},
                "_c": {"type": "number"},
                "_d": {"type": "boolean"},
                "_e": {"type": "string"},
            },
            "required": ["a", "_b", "_c", "_d"],
        },
    }


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

    resp = s.handle_message(request("tools/call", {"name": "shout", "arguments": {"word": "hi"}}))
    assert resp["result"] == {"content": [{"type": "text", "text": "HI"}]}

    resp = s.handle_message(request("tools/call", {"name": "quiet"}))  # arguments omitted: no-arg call
    assert resp["result"] == {"content": []}  # None is no content, not the text "null"

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
