from iris_dictation import config


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")
    cfg = config.load()
    assert cfg.key == "KEY_CAPSLOCK"
    assert cfg.output == "type"


def test_file_overrides_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('languages = ["hu", "en"]\nkey = "KEY_RIGHTALT"\n')
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    cfg = config.load()
    assert cfg.languages == ["hu", "en"]
    assert cfg.key == "KEY_RIGHTALT"


def test_unknown_keys_are_skipped_with_a_warning(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    path.write_text('lnaguages = ["hu"]\n')
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    cfg = config.load()
    assert cfg.languages == []
    assert not hasattr(cfg, "lnaguages")
    assert "lnaguages" in caplog.text
