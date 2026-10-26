"""Реестр фич: кнопки главного меню, префиксы callback_data, команды, ввод текста.

Новая фича = новый файл в rudn/features/ с объектом `feature = Feature(...)`
и одна строка в rudn/features/__init__.py. Ядро трогать не нужно.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Router

    from .ui import Ctx

Handler = Callable[["Ctx", list[str]], Awaitable[None]]
CommandHandler = Callable[["Ctx"], Awaitable[None]]
TextHandler = Callable[["Ctx", str], Awaitable[None]]
SummaryFn = Callable[["Ctx"], list[str]]

CALLBACK_LIMIT = 64
PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")


class RegistryError(RuntimeError):
    pass


@dataclass
class MenuButton:
    """Кнопка главного меню. `text` может быть функцией (динамическая подпись);
    если функция вернула None/"" — кнопка скрыта. Кнопки с одинаковым `row`
    стоят в одном ряду (до 3), ряды упорядочены по минимальному `order`."""

    text: str | Callable[["Ctx"], str | None]
    callback: str
    order: int = 100
    row: str | None = None
    owner_only: bool = False


@dataclass
class CommandSpec:
    name: str
    description: str
    handler: CommandHandler
    hidden: bool = False  # не показывать в меню команд Telegram


@dataclass
class Feature:
    name: str
    callbacks: dict[str, Handler] = field(default_factory=dict)
    menu: list[MenuButton] = field(default_factory=list)
    menu_dynamic: Callable[["Ctx"], list[MenuButton]] | None = None
    commands: list[CommandSpec] = field(default_factory=list)
    summary: SummaryFn | None = None
    summary_order: int = 100
    text_states: dict[str, TextHandler] = field(default_factory=dict)
    router: "Router | None" = None


def prefix_of(callback_data: str) -> str:
    return callback_data.split("|", 1)[0].split(":", 1)[0]


class Registry:
    def __init__(self) -> None:
        self.features: list[Feature] = []
        self.callbacks: dict[str, tuple[str, Handler]] = {}
        self.commands: dict[str, tuple[str, CommandSpec]] = {}
        self.text_states: dict[str, tuple[str, TextHandler]] = {}
        self.sealed = False

    def add(self, feature: Feature) -> None:
        if self.sealed:
            raise RegistryError("реестр уже закрыт")
        if any(f.name == feature.name for f in self.features):
            raise RegistryError(f"фича {feature.name!r} подключена дважды")
        for prefix, handler in feature.callbacks.items():
            if not PREFIX_RE.match(prefix):
                raise RegistryError(f"{feature.name}: недопустимый префикс {prefix!r}")
            if prefix in self.callbacks:
                raise RegistryError(
                    f"префикс {prefix!r} занят фичей {self.callbacks[prefix][0]!r}, конфликт с {feature.name!r}"
                )
            if not callable(handler):
                raise RegistryError(f"{feature.name}: обработчик {prefix!r} не вызываемый")
            self.callbacks[prefix] = (feature.name, handler)
        for command in feature.commands:
            if not COMMAND_RE.match(command.name):
                raise RegistryError(f"{feature.name}: недопустимая команда {command.name!r}")
            if command.name in self.commands:
                raise RegistryError(f"команда /{command.name} уже есть в {self.commands[command.name][0]!r}")
            if not 1 <= len(command.description) <= 256:
                raise RegistryError(f"/{command.name}: описание 1..256 символов")
            self.commands[command.name] = (feature.name, command)
        for state, handler in feature.text_states.items():
            if state in self.text_states:
                raise RegistryError(f"состояние ввода {state!r} уже есть в {self.text_states[state][0]!r}")
            self.text_states[state] = (feature.name, handler)
        self.features.append(feature)

    def check_callback(self, data: str) -> None:
        if len(data.encode("utf-8")) > CALLBACK_LIMIT:
            raise RegistryError(f"callback_data длиннее {CALLBACK_LIMIT} байт: {data!r}")
        if prefix_of(data) not in self.callbacks:
            raise RegistryError(f"нет обработчика для callback_data {data!r}")
        back = data.split("|", 1)[1] if "|" in data else None
        if back is not None and prefix_of(back) not in self.callbacks:
            raise RegistryError(f"нет обработчика для «назад» {back!r}")

    def validate(self) -> None:
        for feature in self.features:
            for button in feature.menu:
                self.check_callback(button.callback)
        self.sealed = True

    def handler(self, prefix: str) -> Handler | None:
        found = self.callbacks.get(prefix)
        return found[1] if found else None

    def summaries(self) -> list[SummaryFn]:
        items = [(f.summary_order, i, f.summary) for i, f in enumerate(self.features) if f.summary]
        return [fn for _, _, fn in sorted(items, key=lambda x: (x[0], x[1]))]

    def visible_commands(self) -> list[CommandSpec]:
        return [spec for _, spec in self.commands.values() if not spec.hidden]


REGISTRY = Registry()
