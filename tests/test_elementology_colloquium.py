"""Игра «⚛️ Элементы» (коллоквиум №1): данные — content/elementology/colloquium1.json (копия того, что вшито в GitHub Pages)."""
import json
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parents[1] / "content" / "elementology" / "colloquium1.json").read_text(encoding="utf-8"))
BLOCKS = [b for b, _ in DATA["blocks"]]


def test_blocks_in_lecture_order():
    assert BLOCKS == ["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"]
    assert "⚠" in dict(DATA["blocks"])["b7"]  # стоматология помечена


def test_questions_have_answer_and_slide_source():
    ids = set()
    for q in DATA["questions"]:
        assert q["id"] not in ids
        ids.add(q["id"])
        assert q["block"] in BLOCKS
        assert q["question"].strip() and len(q["answer"]) > 40
        assert "сл." in q["source"], q["id"]
    for b in BLOCKS:
        assert sum(q["block"] == b for q in DATA["questions"]) >= 3, b


def test_quiz_mcq_valid():
    ids = set()
    for z in DATA["quiz"]:
        assert z["id"] not in ids
        ids.add(z["id"])
        assert z["block"] in BLOCKS
        assert len(z["options"]) == 4 and len(set(z["options"])) == 4
        assert 0 <= z["correct"] < 4
        assert "сл." in z["source"] and z["explain"].strip()


def test_mock_colloquium_possible_without_dentistry():
    # пробный коллоквиум берёт 5 вопросов из разных блоков, без b7 по умолчанию
    assert len({q["block"] for q in DATA["questions"] if q["block"] != "b7"}) >= 5


def test_game_url_matches_button():
    from rudn.core.ui import GAMES
    assert dict(GAMES)["⚛️ Элементы"].startswith(DATA["game"])
