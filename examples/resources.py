"""Resources: a plain text read and a dict served as JSON."""

from mcpatom import Server

srv = Server("resources-example")


@srv.resource("data://motd")
def motd() -> str:
    """Message of the day."""
    return "Be indivisible."


@srv.resource("data://config", mime_type="application/json")
def config() -> dict:
    """Server configuration."""
    return {"theme": "dark", "retries": 3}  # dicts are serialised to JSON text


srv.serve_stdio()
