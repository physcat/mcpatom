import io
import json

import mcpatom
from mcpatom import Server


def srv(**kwargs):
    return Server("test-server", version="0.0.1", **kwargs)


def request(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_initialize():
    s = srv(instructions="Search before you modify.")
    for version in mcpatom.PROTOCOL_VERSIONS:
        resp = s.handle_message(request("initialize", {"protocolVersion": version}, id=7))
        assert resp == {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "protocolVersion": version,
                "capabilities": {},
                "serverInfo": {"name": "test-server", "version": "0.0.1"},
                "instructions": "Search before you modify.",
            },
        }

    # An unknown version gets a counter-offer, never a rejection.
    resp = srv().handle_message(request("initialize", {"protocolVersion": "2024-11-05"}))
    assert resp["result"]["protocolVersion"] == "2025-06-18"


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
