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
