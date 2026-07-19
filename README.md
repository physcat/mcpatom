# mcpatom

A minimal Python [MCP](https://modelcontextprotocol.io) Server SDK.

From Greek *atomos*: indivisible. The whole library is a single file,
`mcpatom.py`, consumable two ways:

- **Copy the file** into your project. No dependency, no lockfile entry.
- **Install the package**: `pip install mcpatom` - the file itself is
  the installed module.

## Scope

- Tools, resources, and prompts, over stdio or streamable HTTP.
- Protocol versions `2025-06-18` and `2025-11-25`.
- stdlib-only Python >= 3.10, no dependencies, ever.

Everything else (sampling, elicitation, subscriptions, auth, etc.) is deliberately omitted.

## Usage

```python
from mcpatom import Server

srv = Server("my-server")


@srv.tool
def greet(name: str, excited: bool = False) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}{'!' if excited else '.'}"


@srv.resource("data://motd")
def motd() -> str:
    """Message of the day."""
    return "Be indivisible."


@srv.prompt
def haiku(topic: str) -> str:
    """Ask for a haiku."""
    return f"Write a haiku about {topic}."


srv.serve_stdio()
```

For streamable HTTP instead of stdio, end with:

```python
srv.serve_http(8388)  # http://127.0.0.1:8388/mcp
```

`serve_http` binds to loopback and rejects DNS-rebinding requests;
`host="0.0.0.0"` widens the bind and switches those checks off.

More runnable servers in [examples/](examples/).

## Schemas and return types

Each function's name, docstring, and annotations become the tool's name,
description, and schema; `@srv.tool(name=, description=, input_schema=,
output_schema=)` override generation, and `extra=` merges raw fields into
the listing. `Server()` also takes `version=` and `instructions=`.
Parameters may be annotated with any of these:
- `str`
- `int`
- `float`
- `bool`
- `list[X]` (or bare `list`)
- `Literal[...]`
- `TypedDict`
- `X | None` of any of the above

`Annotated[X, "text"]` adds a description the model sees.

A tool may return:

- `str` - one text block
- `dict` - JSON text plus `structuredContent`; a `TypedDict` return
  annotation publishes the matching `outputSchema`
- `Image(data, mime_type)` / `Audio(data, mime_type)` - one binary
  block (raw bytes in, base64 on the wire)
- a tuple - one block per item: `str` text, `Image`/`Audio` media, a
  dict whose `"type"` names a spec block type verbatim (e.g.
  `resource_link`)
- `None` - empty content; any other JSON value - text

Raising is the error API: any exception becomes `isError` content the
model can read and correct.

Resources return `str` (text) or `bytes` (base64 blob).

Prompts take only `str` arguments and return a `str` user message, or a
list of message dicts passed through verbatim.

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
# {"jsonrpc":"2.0","id":1,"result":{}}
```

