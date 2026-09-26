"""The vocabulary: reading the file and applying taught phrases. Made-up
words only: real vocabulary is private and never belongs in this repository."""

import os

import pytest

from iris_dictation.vocabulary import VocabularyFile, replace_heard


def test_reloads_on_change(tmp_path):
    path = tmp_path / "vocabulary.toml"
    vocab = VocabularyFile(path)
    assert vocab.heard() == {}                             # no file yet
    path.write_text('[heard]\n"Zorbax" = "Zorbex"\n')
    assert vocab.heard() == {"Zorbax": "Zorbex"}
    path.write_text('[heard]\n"quilo" = "Quillo"\n')
    os.utime(path, (1, 1))                                 # a different mtime for sure
    assert vocab.heard() == {"quilo": "Quillo"}


def test_broken_file_keeps_the_previous_one(tmp_path):
    path = tmp_path / "vocabulary.toml"
    path.write_text('[heard]\n"Zorbax" = "Zorbex"\n')
    vocab = VocabularyFile(path)
    vocab.heard()
    path.write_text("[heard\n")
    os.utime(path, (2, 2))
    assert vocab.heard() == {"Zorbax": "Zorbex"}


@pytest.mark.parametrize("text, expected", [
    ("Comitant Push.", "Commit and Push."),                    # sentence start keeps its capital
    ("then comitant push please", "then commit and push please"),
    ("Check Zorbax.example please.", "Check zorbex.example please."),
    ("the zorbaxes", "the zorbaxes"),                          # whole words only
])
def test_replace_heard(text, expected):
    heard = {"comitant": "commit and", "Zorbax.example": "zorbex.example", "zorbax": "Zorbex"}
    assert replace_heard(text, heard) == expected
