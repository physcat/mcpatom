"""Tools: typed parameters, a structured (dict) return, an image return, and
hand-written schema overrides."""

import random
from pathlib import Path
from typing import Annotated, Literal

from mcpatom import Image, Server

srv = Server("tools-example")


@srv.tool
def add(a: float, b: float) -> str:
    """Add two numbers."""
    return str(a + b)


@srv.tool
def roll(sides: Annotated[Literal[6, 20], "Die size"] = 6) -> dict:
    """Roll a die."""
    return {"sides": sides, "value": random.randint(1, sides)}


@srv.tool(
    input_schema={
        "type": "object",
        "properties": {"celsius": {"type": "number", "minimum": -273.15}},
        "required": ["celsius"],
    },
    output_schema={
        "type": "object",
        "properties": {"fahrenheit": {"type": "number"}},
        "required": ["fahrenheit"],
    },
)
def convert(celsius: float) -> dict:
    """Convert Celsius to Fahrenheit."""
    return {"fahrenheit": celsius * 9 / 5 + 32}


@srv.tool
def image() -> Image:
    """A mystery image."""
    return Image(Path(__file__).with_name("cat.gif").read_bytes(), "image/gif")


srv.serve_stdio()
