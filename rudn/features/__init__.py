"""Список подключённых фич. Порядок = порядок в главном меню и сводке
(точный порядок кнопок задаёт `order` у MenuButton).

Чтобы добавить пункт главного меню: создай rudn/features/<имя>.py с объектом
`feature = Feature(...)` и допиши "<имя>" сюда. Подробно — README.md.
"""

FEATURES: list[str] = [
    "schedule",
    "deadlines",
    "exams",
    "subjects",
    "scores",
    "item",
    "pages",
    "admin",
]
