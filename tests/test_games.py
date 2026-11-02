from rudn.core.ui import GAMES, app_btn, games_row


def test_games_are_https_web_apps():
    row = games_row()
    assert len(row) == 2
    for btn, (_, url) in zip(row, GAMES):
        assert btn.web_app is not None and btn.web_app.url == url
        assert url.startswith("https://rusovezov33-code.github.io/")


def test_app_btn_falls_back_to_url():
    btn = app_btn("x", "http://example.com")
    assert btn.web_app is None and btn.url == "http://example.com"


def test_lessons_screen_has_games_row():
    import inspect
    from rudn.features import lessons
    assert "games_row()" in inspect.getsource(lessons.screen_today)
