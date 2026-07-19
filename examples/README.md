# Examples

Each file is a complete server. Run from the repo root:

```sh
python -m examples.tools
```

or copy `mcpatom.py` next to the example and run it directly.

- `tools.py` - typed parameters, a structured (dict) return, an image
  return, and hand-written schema overrides
- `resources.py` - a text resource and a dict served as JSON
- `prompts.py` - a string prompt and a multi-message prompt
- `streamable_http.py` - the same idea over streamable HTTP

Smoke test a stdio example by piping a request in:

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m examples.tools
```

For `streamable_http.py`, start it, then:

```sh
curl -s localhost:8388/mcp -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

Or lend one to Claude Code for a single session:

```sh
claude --mcp-config '{"mcpServers":{"ex":{"command":"python","args":["-m","examples.tools"]}}}'
```
