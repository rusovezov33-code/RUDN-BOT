"""Список подключённых фич. Порядок = порядок в главном меню и сводке
(точный порядок кнопок задаёт `order` у MenuButton).

Чтобы добавить пункт главного меню: создай rudn/features/<имя>.py с объектом
`feature = Feature(...)` и допиши "<имя>" сюда. Подробно — README.md.
"""

FEATURES: list[str] = [
    "soon",
    "schedule",
    "notes",
    "deadlines",
    "exams",
    "subjects",
    "scores",
    "item",
    "pages",
    "lessons",  # до admin: её router ловит стикеры/фото во время урока раньше admin
    "admin",
    "more",  # «⋯ Ещё» — последним: ссылается на pages/scores/admin
]
