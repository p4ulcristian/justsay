"""Format mode without a model: the fixed rules, reading the vocabulary, and
what happens when the model is missing. Made-up words only: real vocabulary
is private and never belongs in this repository."""

import pytest

from iris_dictation import formatter
from iris_dictation.formatter import Formatter, Vocabulary, VocabularyFile, clean, rules


@pytest.mark.parametrize("said, expected", [
    ("Snake case total order count.", "total_order_count"),
    ("kebab case main nav bar", "main-nav-bar"),
    ("Camel case get user name", "getUserName"),
    ("Pascal case user settings panel", "UserSettingsPanel"),
    ("Constant max buffer size", "MAX_BUFFER_SIZE"),
    ("git status dash dash short", "git status --short"),
    ("docker compose up dash d", "docker compose up -d"),
])
def test_rules_settle_it(said, expected):
    assert rules(said) == (expected, True)


@pytest.mark.parametrize("said, expected", [
    ("john dot doe at example dot com", "john.doe@example.com"),
    ("slash etc slash hosts", "/etc/hosts"),
    ("local host colon three thousand", "local host:3000"),
    ("docs dot example dot org slash three", "docs.example.org/3"),
    ("Method get user by ID.", "Method get user by ID"),
])
def test_rules_leave_the_rest_to_the_model(said, expected):
    assert rules(said) == (expected, False)


@pytest.mark.parametrize("reply, expected", [
    ("getUserById", "getUserById"),
    ("`getUserById`", "getUserById"),
    ('"https://example.com".', "https://example.com"),
    ("```\nfoo_bar\n```", "foo_bar"),
    ("fooBar\nThis is camel case.", "fooBar"),
    ("", ""),
])
def test_clean(reply, expected):
    assert clean(reply) == expected


def test_vocabulary_file_reloads_on_change(tmp_path):
    path = tmp_path / "vocabulary.toml"
    vocab = VocabularyFile(path)
    assert vocab.get() == Vocabulary()                     # no file yet
    path.write_text('words = ["Zorbex"]\n[heard]\n"zor bex" = "zorbex.example"\n')
    assert vocab.get().words == ["Zorbex"]
    assert vocab.get().heard == {"zor bex": "zorbex.example"}
    path.write_text('words = ["Quillo"]\n')
    import os
    os.utime(path, (1, 1))                                 # a different mtime for sure
    assert vocab.get().words == ["Quillo"]


def test_broken_vocabulary_keeps_the_previous_one(tmp_path):
    path = tmp_path / "vocabulary.toml"
    path.write_text('words = ["Zorbex"]\n')
    vocab = VocabularyFile(path)
    vocab.get()
    path.write_text("words = [unclosed\n")
    import os
    os.utime(path, (2, 2))
    assert vocab.get().words == ["Zorbex"]


def make(tmp_path, monkeypatch, reply=None, error=None, vocabulary=""):
    path = tmp_path / "vocabulary.toml"
    path.write_text(vocabulary)
    f = Formatter("http://127.0.0.1:9/v1", "some-model", 1, VocabularyFile(path))
    asked = []

    def ask(msgs, max_tokens):
        asked.append(msgs)
        if error:
            raise error
        return reply
    monkeypatch.setattr(f, "_ask", ask)
    return f, asked


def test_model_gets_what_the_rules_left(tmp_path, monkeypatch):
    f, asked = make(tmp_path, monkeypatch, reply="`john.doe@example.com`")
    assert f.format("john dot doe at example dot com") == "john.doe@example.com"
    assert asked[0][-1] == {"role": "user", "content": "john.doe@example.com"}


def test_rules_alone_skip_the_model(tmp_path, monkeypatch):
    f, asked = make(tmp_path, monkeypatch, reply="never used")
    assert f.format("snake case max retry count") == "max_retry_count"
    assert asked == []


def test_heard_entry_wins_without_the_model(tmp_path, monkeypatch):
    f, asked = make(tmp_path, monkeypatch, reply="wrong",
                    vocabulary='[heard]\n"zor bex dot example" = "zorbex.example"\n')
    assert f.format("Zor Bex dot example.") == "zorbex.example"
    assert asked == []


def test_vocabulary_reaches_the_model(tmp_path, monkeypatch):
    f, asked = make(tmp_path, monkeypatch, reply="quillo.example",
                    vocabulary='words = ["Quillo"]\n[heard]\n"quill oh" = "quillo.example"\n')
    f.format("the quill oh page")
    system, *examples = asked[0]
    assert "Known words: Quillo, quillo.example" in system["content"]
    assert {"role": "assistant", "content": "quillo.example"} in examples


def test_no_model_falls_back_to_the_rules(tmp_path, monkeypatch):
    f, _ = make(tmp_path, monkeypatch, error=OSError("connection refused"))
    assert f.format("john dot doe at example dot com") == "john.doe@example.com"


def test_examples_are_shaped_like_the_input():
    msgs = formatter.messages("x", Vocabulary())
    users = [m["content"] for m in msgs[1:-1] if m["role"] == "user"]
    assert "example.org web page URL" in users              # "dot" already a "."
