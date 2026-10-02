"""Registry for callable vision tools and their public descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    description: str = ""
    input_kinds: tuple[str, ...] = ("image", "video", "camera")


class ToolRegistry:
    """Stores registered handlers and exposes descriptors in registration order."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor, handler: Callable[..., Any]) -> None:
        if not isinstance(descriptor, ToolDescriptor):
            raise TypeError("descriptor must be a ToolDescriptor")
        if not descriptor.name or not descriptor.name.strip():
            raise ValueError("tool name must be a non-empty string")
        if descriptor.name in self._handlers:
            raise ValueError(f"tool {descriptor.name!r} is already registered")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not isinstance(descriptor.input_kinds, tuple) or not descriptor.input_kinds:
            raise ValueError("input_kinds must be a non-empty tuple")
        supported = {"image", "video", "camera"}
        if any(kind not in supported for kind in descriptor.input_kinds):
            raise ValueError("input_kinds contains an unsupported input kind")
        self._descriptors[descriptor.name] = descriptor
        self._handlers[descriptor.name] = handler

    def resolve(self, name: str) -> Callable[..., Any]:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._descriptors.values())

    def descriptor(self, name: str) -> ToolDescriptor:
        try:
            return self._descriptors[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc


__all__ = ["ToolDescriptor", "ToolRegistry"]
