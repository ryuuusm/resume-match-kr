"""Tests for _textmatch: upstream boundary rules, synonyms, particles, Hangul substring guard."""
import json
import unicodedata
from pathlib import Path

import pytest

from _textmatch import Matcher, normalize

SYN = json.loads((Path(__file__).resolve().parent.parent / "skills" / "resume-match-kr" / "data" / "synonyms.json")
                 .read_text(encoding="utf-8"))["groups"]


@pytest.fixture(scope="module")
def m():
    return Matcher(SYN)


def has(m, kw, text):
    return m.contains(kw, normalize(text))


# --- upstream behaviour kept -------------------------------------------------
def test_latin_word_boundaries(m):
    assert has(m, "python", "I use Python daily")
    assert not has(m, "python", "pythonic code")
    assert not has(m, "Java", "JavaScript only")


def test_cjk_adjacent_latin_is_boundary(m):
    assert has(m, "SAP", "SAP기반 시스템")
    assert has(m, "AWS", "AWS에서 운영")


def test_cjk_keyword_is_substring(m):
    assert has(m, "데이터 분석", "데이터 분석을 수행")


# --- additions ---------------------------------------------------------------
def test_synonyms_both_directions(m):
    assert has(m, "Python", "파이썬으로 자동화")
    assert has(m, "쿠버네티스", "Kubernetes cluster ops")
    assert has(m, "Kubernetes", "k8s 운영")


def test_hangul_inside_longer_known_term_rejected(m):
    # '자바' (Java) must not match inside '자바스크립트' (JavaScript)...
    assert not has(m, "Java", "자바스크립트 개발")
    # ...but still matches when it stands on its own somewhere else.
    assert has(m, "Java", "자바스크립트와 자바 개발")
    assert has(m, "JavaScript", "자바스크립트 개발")


def test_removed_ambiguous_synonyms(m):
    assert not has(m, "Vue.js", "고객 인터뷰 진행")
    assert not has(m, "Computer System Validation", "CSV 파일 업로드")


def test_particle_fallback_is_conservative(m):
    assert has(m, "데이터를", "데이터 파이프라인")
    # 1-syllable stems are never produced, and '로' is not a stripped particle.
    assert not Matcher([]).contains("마이크로", normalize("마이크 테스트"))


def test_nfd_input_normalised(m):
    nfd = unicodedata.normalize("NFD", "파이썬 경험")
    assert has(m, "Python", nfd)


def test_no_synonyms_mode():
    assert not Matcher([]).contains("Python", normalize("파이썬"))
