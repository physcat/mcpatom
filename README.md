# mcpatom

A minimal [MCP](https://modelcontextprotocol.io) server in one
stdlib-only Python module. Python >= 3.10, no dependencies, ever.

```python
from mcpatom import Server

srv = Server("my-server")


@srv.tool
def greet(name: str, excited: bool = False) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}{'!' if excited else '.'}"


srv.serve_stdio()
```

For streamable HTTP instead of stdio, end with:

```python
srv.serve_http(8388)  # http://127.0.0.1:8388/mcp
```

`serve_http` binds to loopback and rejects DNS-rebinding requests;
`host="0.0.0.0"` widens the bind and switches those checks off.

## Wiring it up

```sh
claude mcp add my-server -- /abs/path/.venv/bin/python /abs/path/server.py
```

or in any `mcpServers` config:

```json
{"mcpServers": {"my-server": {"command": "/abs/path/.venv/bin/python", "args": ["/abs/path/server.py"]}}}
```

Smoke test without a client:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"ping"}' | python server.py
```

