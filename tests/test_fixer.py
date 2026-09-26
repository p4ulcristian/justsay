"""The second pass without a model: the guardrail, the vocabulary file, and
the fallbacks. Made-up words only: real vocabulary is private and never
belongs in this repository."""

import os

import pytest

from iris_dictation.fixer import Fixer, Vocabulary, VocabularyFile, allowed, messages, worth_asking

KNOWN = ["Zorbex", "Quillo", "foobartools.com"]


@pytest.mark.parametrize("before, after", [
    ("I pushed it to Zorbax.", "I pushed it to Zorbex."),                    # known word
    ("The site is foo bar tools dot com.", "The site is foobartools.com."),  # joined
    ("So, um, the tests pass.", "So, the tests pass."),                      # filler
    ("Monday, no wait, Tuesday.", "Tuesday."),                                # self-correction
    ("Open the second, I mean the third file.", "Open the third file."),
    ("hello there", "Hello there."),                                          # case, punctuation
])
def test_allowed(before, after):
    assert allowed(before, after, KNOWN)


@pytest.mark.parametrize("before, after", [
    ("Holnap reggel megyek a boltba.", "Holnap reggel megyek a Quillo oldalt."),  # invented
    ("What time is it?", "It is noon."),                                          # answered
    ("Good morning.", "Jó reggelt."),                                             # translated
    ("I like the red one.", "I like the blue one."),                              # changed
    ("Deploy it now.", "Deploy it now, please."),                                 # added
    ("Remove the old logs.", "Remove the logs."),                                 # dropped
    ("Ask Mark about it.", "Ask Zorbex about it."),                               # not similar
])
def test_not_allowed(before, after):
    assert not allowed(before, after, KNOWN)


def test_worth_asking():
    empty = Vocabulary()
    assert not worth_asking("Plain sentence here.", empty)
    assert worth_asking("So, um, yes.", empty)
    assert worth_asking("Monday, no wait, Tuesday.", empty)
    assert worth_asking("Plain sentence here.", Vocabulary(words=["Zorbex"]))


def test_vocabulary_file_reloads_on_change(tmp_path):
    path = tmp_path / "vocabulary.toml"
    vocab = VocabularyFile(path)
    assert vocab.get() == Vocabulary()                     # no file yet
    path.write_text('words = ["Zorbex"]\n[heard]\n"Zorbax" = "Zorbex"\n')
    assert vocab.get().known() == ["Zorbex"]
    path.write_text('words = ["Quillo"]\n')
    os.utime(path, (1, 1))                                 # a different mtime for sure
    assert vocab.get().words == ["Quillo"]


def test_broken_vocabulary_keeps_the_previous_one(tmp_path):
    path = tmp_path / "vocabulary.toml"
    path.write_text('words = ["Zorbex"]\n')
    vocab = VocabularyFile(path)
    vocab.get()
    path.write_text("words = [unclosed\n")
    os.utime(path, (2, 2))
    assert vocab.get().words == ["Zorbex"]


def test_messages_carry_the_vocabulary():
    msgs = messages("I pushed to Zorbax.", Vocabulary(words=["Zorbex"], heard={"Zorbax": "Zorbex"}))
    assert msgs[-1]["content"] == "Known words: Zorbex\nText: I pushed to Zorbax."
    assert {"role": "user", "content": "Known words: Zorbex\nText: Zorbax"} in msgs


def make(tmp_path, monkeypatch, reply=None, error=None, vocabulary='words = ["Zorbex"]\n'):
    path = tmp_path / "vocabulary.toml"
    path.write_text(vocabulary)
    f = Fixer("http://127.0.0.1:9/v1", "some-model", 1, VocabularyFile(path))
    asked = []

    def ask(msgs, max_tokens):
        asked.append(msgs)
        if error:
            raise error
        return reply
    monkeypatch.setattr(f, "_ask", ask)
    return f, asked


def test_fix_uses_an_allowed_answer(tmp_path, monkeypatch):
    f, _ = make(tmp_path, monkeypatch, reply="I pushed it to Zorbex.")
    assert f.fix("I pushed it to Zorbax.") == "I pushed it to Zorbex."


def test_fix_rejects_a_bad_answer(tmp_path, monkeypatch):
    f, _ = make(tmp_path, monkeypatch, reply="I pushed it to production.")
    assert f.fix("I pushed it to Zorbax.") == "I pushed it to Zorbax."


def test_no_model_types_whispers_text(tmp_path, monkeypatch):
    f, _ = make(tmp_path, monkeypatch, error=OSError("connection refused"))
    assert f.fix("I pushed it to Zorbax.") == "I pushed it to Zorbax."


def test_nothing_to_fix_skips_the_model(tmp_path, monkeypatch):
    f, asked = make(tmp_path, monkeypatch, reply="never used", vocabulary="")
    assert f.fix("A plain sentence.") == "A plain sentence."
    assert asked == []
