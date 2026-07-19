"""A greeter served over streamable HTTP instead of stdio."""

from mcpatom import Server

srv = Server("http-example")


@srv.tool
def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}."


srv.serve_http(8388)  # POST http://127.0.0.1:8388/mcp
