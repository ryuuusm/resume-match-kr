"""Tests for match.py: weights, sub-scores, Korean parsing, grounding check, CLI."""
import json
from pathlib import Path

import pytest

import match
from _textmatch import Matcher

SKILL = Path(__file__).resolve().parent.parent / "skills" / "resume-match-kr"
EX_RESUME = SKILL / "templates" / "resume.example.md"
EX_JD = SKILL / "templates" / "jd-keywords.example.json"


@pytest.fixture(scope="module")
def m():
    return Matcher(match.load_synonyms(None))


def jd(**kw):
    base = {"role": "", "role_aliases": [], "required_skills": [], "preferred_skills": [], "keywords": []}
    base.update(kw)
    return base


def test_weights_sum_to_one():
    assert sum(match.WEIGHTS.values()) == pytest.approx(1.0)


def test_example_scores_are_hand_checked(m):
    r = match.score(EX_RESUME.read_text(encoding="utf-8"), match.load_jd(str(EX_JD)), m)
    s = r["sub_scores"]
    assert s["keyword_match"] == pytest.approx(76.9)       # 10 of 13 terms
    assert s["skills_coverage"] == pytest.approx(66.7)     # 6 of 9 skills
    assert s["title_match"] == 100.0                        # alias "IT 프로젝트 매니저" in headline
    assert s["section_completeness"] == 100.0
    assert s["date_consistency"] == 100.0                   # all YYYY.MM
    assert r["overall_score"] == pytest.approx(84.1)
    assert r["missing_required"] == ["Project Management", "Stakeholder Management"]
    assert any("필수 요건 누락" in t for t in r["recommendations"])


def test_weight_redistribution_when_not_scorable(m):
    # No role, no dates -> title/date not scorable; weights renormalise over the rest.
    text = "# A\nheadline\n## Skills\nPython\n"
    r = match.score(text, jd(required_skills=["Python"]), m)
    assert r["sub_scores"]["title_match"] is None and r["sub_scores"]["date_consistency"] is None
    # keyword 100, skills 100, sections (skills only, no contact) = 1/5 = 20
    expected = (100 * .4 + 100 * .2 + 20 * .15) / (.4 + .2 + .15)
    assert r["overall_score"] == pytest.approx(round(expected, 1))


def test_title_levels(m):
    j = jd(role="Data Engineer", keywords=["SQL"])
    assert match.score("# X\nData Engineer\n", j, m)["sub_scores"]["title_match"] == 100.0
    assert match.score("# X\nAnalyst\n## Summary\nI am a data engineer\n", j, m)["sub_scores"]["title_match"] == 75.0
    assert match.score("# X\nSenior Data Analyst\n", j, m)["sub_scores"]["title_match"] == 25.0


def test_korean_dates_and_mixed_styles(m):
    text = "# X\nPM\n## 경력\n- A사 (2019년 3월 - 현재)\n- B사 (2015.01 - 2019.02)\n"
    r = match.score(text, jd(keywords=["PM"]), m)
    assert r["date_styles"] == {"korean": 1, "dot": 2}
    assert r["sub_scores"]["date_consistency"] == pytest.approx(66.7)


def test_korean_section_headings(m):
    text = "# X\n직함\nx@y.com\n## 요약\n.\n## 경력\n.\n## 학력\n.\n## 보유 기술\nSQL\n"
    assert match.score(text, jd(keywords=["SQL"]), m)["sub_scores"]["section_completeness"] == 100.0


@pytest.mark.parametrize("text,expected", [
    ("010-1234-5678", True), ("02-123-4567", True), ("+82 10 1234 5678", True),
    ("2015 2019", False), ("2019.03 - 2023.06", False),
])
def test_phone_detection(text, expected):
    assert bool(match.PHONE_RE.search(text)) is expected


def test_skills_section_parsing():
    r = match.parse_resume("# X\nT\n## 기술\n- 언어: Python, SQL / Java\n- ERP · SAP\n")
    assert r["skills"] == ["Python", "SQL", "Java", "ERP", "SAP"]


def test_grounding_check_flags_unsupported(m):
    master = "# X\nPM\n## 기술\nSAP, Python\n"
    tailored = "# X\nPM\n## 기술\nSAP, Python, Kubernetes\n"
    r = match.score(tailored, jd(required_skills=["SAP", "Kubernetes", "SQL"]), m, master)
    assert r["unsupported_vs_master"] == ["Kubernetes"]
    assert r["injectable_from_master"] == []
    r2 = match.score("# X\nPM\n## 기술\nSAP\n", jd(required_skills=["SAP", "Python"]), m, master)
    assert r2["injectable_from_master"] == ["Python"]


@pytest.mark.parametrize("bad", [
    {"role": 5, "keywords": ["a"]},
    {"keywords": "SAP"},
    {"keywords": [""]},
    {"keywords": ["x" * 101]},
    {"role": "PM"},
    {"keywords": ["k"] * 201},
])
def test_invalid_jd_rejected(tmp_path, bad):
    p = tmp_path / "jd.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(match.InputError):
        match.load_jd(str(p))


def test_cli(tmp_path, capsys):
    assert match.main([str(EX_RESUME), "--jd", str(EX_JD)]) == 0
    assert "종합 점수" in capsys.readouterr().out
    assert match.main([str(EX_RESUME), "--jd", str(EX_JD), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overall_score"] == pytest.approx(84.1)
    # grounding failure -> exit 1
    master = tmp_path / "master.md"
    master.write_text("# X\nPM\n", encoding="utf-8")
    assert match.main([str(EX_RESUME), "--jd", str(EX_JD), "--master", str(master)]) == 1
    # input errors -> exit 2
    assert match.main([str(tmp_path / "nope.md"), "--jd", str(EX_JD)]) == 2
    binary = tmp_path / "r.pdf"
    binary.write_bytes(b"%PDF-1.7\xff\xfe\x00")
    assert match.main([str(binary), "--jd", str(EX_JD)]) == 2
