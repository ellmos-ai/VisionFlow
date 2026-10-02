from __future__ import annotations

import pytest

from visionflow.registry import ToolDescriptor, ToolRegistry


def test_register_and_resolve_returns_registered_handler():
    registry = ToolRegistry()
    handler = lambda image: image

    registry.register(ToolDescriptor("identity"), handler)

    assert registry.resolve("identity") is handler


def test_descriptors_exports_registered_descriptors_in_order():
    registry = ToolRegistry()
    first = ToolDescriptor("first")
    second = ToolDescriptor("second", description="second tool")
    registry.register(first, lambda value: value)
    registry.register(second, lambda value: value)

    assert registry.descriptors == (first, second)


def test_duplicate_tool_name_is_rejected():
    registry = ToolRegistry()
    registry.register(ToolDescriptor("same"), lambda value: value)

    with pytest.raises(ValueError, match="same"):
        registry.register(ToolDescriptor("same"), lambda value: value)


def test_unknown_tool_name_is_rejected_by_resolve():
    with pytest.raises(KeyError, match="missing"):
        ToolRegistry().resolve("missing")


def test_non_callable_handler_is_rejected():
    with pytest.raises(TypeError, match="callable"):
        ToolRegistry().register(ToolDescriptor("broken"), object())


def test_default_runtime_registry_exposes_direction_for_video_and_camera():
    from visionflow.runtime import create_registry

    registry = create_registry()
    descriptor = registry.descriptor("direction")

    assert descriptor.input_kinds == ("video", "camera")
    assert callable(registry.resolve("direction"))
