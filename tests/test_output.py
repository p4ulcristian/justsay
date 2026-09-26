from iris_dictation import output


def fake(monkeypatch, type_ok, paste_ok):
    used = []
    monkeypatch.setattr(output, "type_text", lambda t: used.append("type") or type_ok)
    monkeypatch.setattr(output, "paste_text", lambda t: used.append("paste") or paste_ok)
    monkeypatch.setattr(output, "_clipboard_write", lambda d: used.append("clipboard"))
    return used


def test_type_first(monkeypatch):
    used = fake(monkeypatch, True, True)
    assert output.deliver("hi", "type") == "type"
    assert used == ["type"]


def test_falls_back_to_paste_then_clipboard(monkeypatch):
    used = fake(monkeypatch, False, False)
    assert output.deliver("hi", "type") == "clipboard"
    assert used == ["type", "paste", "clipboard"]


def test_no_fallback(monkeypatch):
    used = fake(monkeypatch, False, True)
    assert output.deliver("hi", "type", fallback=False) == "clipboard"
    assert used == ["type", "clipboard"]


def test_clipboard_only(monkeypatch):
    used = fake(monkeypatch, True, True)
    assert output.deliver("hi", "clipboard") == "clipboard"
    assert used == ["clipboard"]
