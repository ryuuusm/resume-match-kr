# Copyright 2026 ryuuusm
# Portions derived from Resume-Matcher (https://github.com/srbhr/Resume-Matcher),
# apps/backend/app/services/text_match.py, licensed under the Apache License 2.0.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# MODIFICATIONS (Apache-2.0 section 4(b)):
# - Text and keywords are NFC-normalised and casefolded (Hangul typed on macOS
#   is often NFD, which would otherwise never match).
# - Added synonym expansion (Korean <-> English groups) and a conservative
#   Korean-particle fallback for keywords.
# - A Hangul hit inside a longer known term from another synonym group is
#   ignored (the Korean counterpart of upstream's 'java' vs 'javascript' rule).
"""Whole-term keyword matching with CJK-aware boundaries and KO/EN synonyms."""
import re
import unicodedata
from typing import Dict, Iterable, List, Optional

# Han (incl. supplementary planes), Kana and Hangul syllables/Jamo — unchanged from upstream.
_CJK_CHAR_CLASS = (
    r"[ᄀ-ᇿ぀-ヿ㄰-㆏ㇰ-ㇿ"
    r"㐀-鿿ꥠ-꥿가-퟿豈-﫿"
    r"ｦ-ￜ\U0001b000-\U0001b16f\U00020000-\U0003ffff]"
)
_CJK_RE = re.compile(_CJK_CHAR_CLASS)

# Particles stripped from the END of a Korean keyword only as a fallback, and only
# when at least two syllables remain ("데이터를" -> "데이터", but "프로" stays "프로").
# Single-syllable particles that commonly end real nouns (로, 과, 이, 가 ...) are
# deliberately excluded to avoid turning "마이크로" into "마이크".
_PARTICLES = ("에서", "으로", "을", "를")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _match_one(keyword: str, text: str) -> bool:
    """Upstream logic: CJK keywords match as substrings, others need word boundaries.

    Han/Kana/Hangul characters also count as boundaries for adjacent Latin terms,
    so 'python' matches '파이썬과python' but not 'pythonic', and 'java' does not
    match 'javascript'. Both arguments must already be normalised.
    """
    if not keyword:
        return False
    if _CJK_RE.search(keyword):
        return keyword in text
    escaped = re.escape(keyword)
    pattern = (
        rf"(?:(?<!\w)|(?<={_CJK_CHAR_CLASS}))"
        rf"{escaped}"
        rf"(?:(?!\w)|(?={_CJK_CHAR_CLASS}))"
    )
    return bool(re.search(pattern, text))


def _particle_stripped(keyword: str) -> Optional[str]:
    for p in _PARTICLES:
        if keyword.endswith(p) and _CJK_RE.search(keyword):
            stem = keyword[: -len(p)].rstrip()
            if len(stem) >= 2:
                return stem
    return None


class Matcher:
    """Keyword matcher with an optional synonym table.

    synonyms: list of groups, each a list of equivalent spellings, e.g.
    [["Python", "파이썬"], ["Kubernetes", "쿠버네티스", "k8s"]].
    """

    def __init__(self, synonyms: Optional[Iterable[Iterable[str]]] = None):
        self._groups: Dict[str, List[str]] = {}
        for group in synonyms or []:
            variants = [normalize(v).strip() for v in group if isinstance(v, str) and v.strip()]
            for v in variants:
                # A spelling may belong to several groups; union them.
                merged = self._groups.setdefault(v, [])
                for w in variants:
                    if w not in merged:
                        merged.append(w)
        # Every known CJK spelling, used to reject a hit that is only part of a
        # longer known term ("자바" inside "자바스크립트").
        self._cjk_terms = sorted({k for k in self._groups if _CJK_RE.search(k)}, key=len, reverse=True)

    def _cjk_hit(self, variant: str, text: str) -> bool:
        """Substring match for a CJK variant, ignoring occurrences inside a longer known term
        that is NOT a synonym of this variant."""
        same_group = set(self._groups.get(variant, [variant]))
        longer = [t for t in self._cjk_terms if len(t) > len(variant) and variant in t and t not in same_group]
        start = text.find(variant)
        while start != -1:
            covered = False
            for term in longer:
                offset = term.find(variant)
                while offset != -1:
                    s = start - offset
                    if s >= 0 and text.startswith(term, s):
                        covered = True
                        break
                    offset = term.find(variant, offset + 1)
                if covered:
                    break
            if not covered:
                return True
            start = text.find(variant, start + 1)
        return False

    def variants(self, keyword: str) -> List[str]:
        key = normalize(keyword).strip()
        out = [key] + [v for v in self._groups.get(key, []) if v != key]
        stem = _particle_stripped(key)
        if stem and stem not in out:
            out.append(stem)
            out += [v for v in self._groups.get(stem, []) if v not in out]
        return out

    def find(self, keyword: str, text: str) -> Optional[str]:
        """Return the variant that matched in text (text must be normalised), else None."""
        for v in self.variants(keyword):
            if _CJK_RE.search(v):
                if self._cjk_hit(v, text):
                    return v
            elif _match_one(v, text):
                return v
        return None

    def contains(self, keyword: str, text: str) -> bool:
        return self.find(keyword, text) is not None
