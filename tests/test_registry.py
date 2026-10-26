import pytest

from rudn.core.registry import REGISTRY, Feature, MenuButton, Registry, RegistryError


async def _noop(ctx, args):
    return None


def test_duplicate_prefix_fails():
    reg = Registry()
    reg.add(Feature("a", callbacks={"zz": _noop}))
    with pytest.raises(RegistryError):
        reg.add(Feature("b", callbacks={"zz": _noop}))


def test_duplicate_feature_and_bad_prefix():
    reg = Registry()
    reg.add(Feature("a"))
    with pytest.raises(RegistryError):
        reg.add(Feature("a"))
    with pytest.raises(RegistryError):
        reg.add(Feature("c", callbacks={"Bad-Prefix": _noop}))


def test_menu_button_without_handler_fails():
    reg = Registry()
    reg.add(Feature("a", callbacks={"aa": _noop}, menu=[MenuButton("x", "nope")]))
    with pytest.raises(RegistryError):
        reg.validate()


def test_callback_limit():
    with pytest.raises(RegistryError):
        REGISTRY.check_callback("d:" + "x" * 70)
    with pytest.raises(RegistryError):
        REGISTRY.check_callback("d:2026-10-26|nope")
    REGISTRY.check_callback("d:2026-10-26|w:2026-10-26")


def test_real_registry():
    assert REGISTRY.sealed
    names = [f.name for f in REGISTRY.features]
    assert names[0] == "home"
    for feature in REGISTRY.features:
        for button in feature.menu:
            REGISTRY.check_callback(button.callback)
    visible = [c.name for c in REGISTRY.visible_commands()]
    assert visible == ["start", "today", "tomorrow", "week", "deadlines", "exams", "subjects"]
    assert "cancel" in REGISTRY.commands and "ask" not in REGISTRY.commands
