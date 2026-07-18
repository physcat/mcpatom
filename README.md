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

