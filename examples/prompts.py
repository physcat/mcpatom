"""Prompts: a plain string reply and a multi-message conversation."""

from mcpatom import Server

srv = Server("prompts-example")


@srv.prompt
def haiku(topic: str) -> str:
    """Ask for a haiku."""
    return f"Write a haiku about {topic}."


@srv.prompt
def review(code: str) -> list:
    """Review code, priming the assistant's opening."""
    return [
        {"role": "user", "content": {"type": "text", "text": f"Review this code:\n{code}"}},
        {"role": "assistant", "content": {"type": "text", "text": "Three issues, most severe first:"}},
    ]


srv.serve_stdio()
