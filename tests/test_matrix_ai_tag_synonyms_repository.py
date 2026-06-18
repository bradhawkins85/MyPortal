from __future__ import annotations

import pytest

from app.repositories import matrix_ai_tag_synonyms as repo


def test_validate_terms_normalises_and_deduplicates():
    assert repo.validate_terms([" Wi-Fi ", "wireless", "wi_fi", ""]) == ["wi fi", "wireless"]


def test_validate_terms_requires_two_unique_terms():
    with pytest.raises(repo.InvalidSynonymGroup):
        repo.validate_terms(["monitor", "monitor"])


def test_validate_terms_limits_term_count():
    with pytest.raises(repo.InvalidSynonymGroup):
        repo.validate_terms([f"term{i}" for i in range(repo.MAX_TERMS_PER_GROUP + 1)])
