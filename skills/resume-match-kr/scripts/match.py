#!/usr/bin/env python3
# Copyright 2026 ryuuusm
# Portions derived from Resume-Matcher (https://github.com/srbhr/Resume-Matcher),
# apps/backend/app/services/ats.py and refiner.py, licensed under the Apache License 2.0.
# Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE in the repository root.
#
# MODIFICATIONS (Apache-2.0 section 4(b)):
# - Works on a Markdown/plain-text resume instead of Resume-Matcher's JSON model:
#   sections, headline, skills and contact info are detected from the text.
# - Korean section headings (경력, 학력, 기술, 요약 ...) and Korean date styles
#   (2019.03, 2019년 3월) are recognised; "현재/재직중" are ignored like "Present".
# - Keyword matching is synonym-aware (data/synonyms.json) via _textmatch.Matcher.
# - JD keywords may carry role_aliases, taken from the posting itself.
# - Added a grounding check: JD terms present in the tailored resume but absent from
#   the master resume are reported as possibly unsupported (exit code 1).
# - Recommendations are rewritten in Korean and never tell the user to add a skill
#   they do not have; a missing REQUIRED skill is always reported.
# - skills_coverage is "not scorable" (None) when the posting lists no skills,
#   instead of upstream's 0.
"""국문·영문 이력서와 채용공고 키워드 매칭 점수 (표준 라이브러리만, 네트워크 없음).

사용법:
    python match.py 이력서.md --jd jd-keywords.json
    python match.py 맞춤이력서.md --jd jd-keywords.json --master 마스터이력서.md
    python match.py 이력서.md --jd jd-keywords.json --json

점수 (Resume-Matcher와 같은 가중치, 평가 불가 항목은 빼고 가중치 재분배):
    keyword_match 40% · skills_coverage 20% · title_match 15% · section_completeness 15% · date_consistency 10%

종료 코드: 0 정상, 1 근거 없는 키워드 의심(--master 사용 시), 2 입력 오류.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _textmatch import Matcher, normalize  # noqa: E402

DEFAULT_SYNONYMS = Path(__file__).resolve().parent.parent / "data" / "synonyms.json"
MAX_INPUT_BYTES = 1024 * 1024
MAX_TERMS = 200
MAX_TERM_LEN = 100

WEIGHTS = {
    "keyword_match": 0.40,
    "skills_coverage": 0.20,
    "title_match": 0.15,
    "section_completeness": 0.15,
    "date_consistency": 0.10,
}
SECTION_PATTERNS = {
    "summary": ["summary", "objective", "profile", "about", "요약", "자기소개", "소개", "프로필", "커리어 요약"],
    "experience": ["experience", "work history", "employment", "경력", "근무", "직무 경험", "경력기술서"],
    "education": ["education", "academic", "degree", "학력", "교육"],
    "skills": ["skills", "technologies", "competencies", "technical", "기술", "스킬", "역량", "자격", "보유"],
}
TITLE_IN_HEADLINE, TITLE_IN_SUMMARY, TITLE_OVERLAP_MAX = 100.0, 75.0, 50.0
TITLE_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "for", "in", "to", "at", "&", "및"})

_YEAR = r"(?:19|20)\d{2}"
_MONTH = (r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
          r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
# Order matters: specific styles before the bare year. 'dot' and 'korean' are additions.
DATE_PATTERN = re.compile(
    rf"(?P<dot>\b{_YEAR}\.\s?\d{{1,2}}\b)"
    rf"|(?P<korean>{_YEAR}년\s?\d{{1,2}}월)"
    rf"|(?P<iso>\b{_YEAR}[-/]\d{{1,2}}\b)"
    rf"|(?P<numeric>\b\d{{1,2}}[/.-]{_YEAR}\b)"
    rf"|(?P<name_year>\b{_MONTH}\.?,?\s+{_YEAR}\b)"
    rf"|(?P<name_short_year>\b{_MONTH}\.?\s+'\d{{2}}\b)"
    rf"|(?P<year>\b{_YEAR}\b)"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Domestic numbers start with 0 (010-1234-5678, 02-123-4567); international ones with +.
# Requiring that prefix keeps year pairs like "2015 2019" from counting as a phone.
PHONE_RE = re.compile(r"(?<![\d+])(?:\+\d{1,3}[ -]?\d{1,4}|0\d{1,2})[ -]?\d{3,4}[ -]?\d{4}(?!\d)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SKILL_SPLIT_RE = re.compile(r"[,，、/|·;]|\s{2,}")


class InputError(Exception):
    pass


# ------------------------------------------------------------------- input

def read_text(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"파일 없음: {path}")
    if p.stat().st_size > MAX_INPUT_BYTES:
        raise InputError(f"파일이 너무 큼(1MB 초과): {path}")
    try:
        return p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(f"UTF-8로 읽을 수 없음: {path}. PDF/DOCX는 먼저 텍스트(Markdown)로 변환한다") from exc


def load_jd(path: str) -> Dict[str, Any]:
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise InputError(f"JD JSON 오류: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError("JD 파일 최상위는 객체여야 함")
    out: Dict[str, Any] = {}
    role = data.get("role")
    if role is not None and (not isinstance(role, str) or len(role) > MAX_TERM_LEN):
        raise InputError("role은 100자 이하 문자열")
    out["role"] = (role or "").strip()
    total = 0
    for field in ("role_aliases", "required_skills", "preferred_skills", "keywords"):
        val = data.get(field) or []
        if not isinstance(val, list) or not all(isinstance(t, str) and 0 < len(t.strip()) <= MAX_TERM_LEN for t in val):
            raise InputError(f"{field}는 1~100자 문자열 목록이어야 함")
        out[field] = [t.strip() for t in val]
        total += len(val)
    if total > MAX_TERMS:
        raise InputError(f"키워드가 너무 많음({total} > {MAX_TERMS})")
    if not any(out[f] for f in ("required_skills", "preferred_skills", "keywords")):
        raise InputError("required_skills / preferred_skills / keywords 중 하나 이상 필요")
    return out


def load_synonyms(path: Optional[str]) -> List[List[str]]:
    if path == "none":
        return []
    data = json.loads(read_text(path or str(DEFAULT_SYNONYMS)))
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list) or not all(isinstance(g, list) for g in groups):
        raise InputError("synonyms 파일에 'groups' 목록이 필요함")
    return groups


# ------------------------------------------------------------------- resume parsing

def classify_heading(title: str) -> Optional[str]:
    t = normalize(title)
    for key, pats in SECTION_PATTERNS.items():
        if any(p in t for p in pats):
            return key
    return None


def parse_resume(text: str) -> Dict[str, Any]:
    """Split a Markdown resume into headline, sections and skills.

    - headline: first non-empty, non-heading, non-contact line after the first H1
      (or the first such line in the file). '|'-separated contact lines are skipped.
    - sections: '##'/'###' headings classified by SECTION_PATTERNS (KO+EN).
    """
    lines = text.splitlines()
    headline = ""
    seen_h1 = not any(re.match(r"^#\s", ln) for ln in lines)
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for ln in lines:
        m = HEADING_RE.match(ln)
        if m:
            level = len(m.group(1))
            if level == 1:
                seen_h1 = True
                current = None
                continue
            kind = classify_heading(m.group(2))
            if kind or level == 2:
                current = kind  # an unrecognised '##' ends the previous section
            continue
        stripped = ln.strip()
        if not headline and seen_h1 and current is None and stripped:
            if not EMAIL_RE.search(stripped) and not PHONE_RE.search(stripped) and not stripped.startswith(">"):
                headline = stripped.lstrip("*_ ").rstrip("*_ ")
        if current:
            sections.setdefault(current, []).append(ln)
    skills: List[str] = []
    for ln in sections.get("skills", []):
        item = re.sub(r"^\s*[-*+]\s*", "", ln)
        item = re.sub(r"^[^:：]{1,20}[:：]\s*", "", item)  # "언어: Python, SQL" -> "Python, SQL"
        skills += [s.strip() for s in SKILL_SPLIT_RE.split(item) if s.strip()]
    return {
        "headline": headline,
        "summary": "\n".join(sections.get("summary", [])),
        "sections": {k: "\n".join(v) for k, v in sections.items()},
        "skills": skills,
        "has_contact": bool(EMAIL_RE.search(text) or PHONE_RE.search(text)),
        "text": text,
    }


# ------------------------------------------------------------------- scoring

def job_terms(jd: Dict[str, Any]) -> List[str]:
    """Distinct JD terms in posting order (case-insensitive dedup)."""
    seen: Dict[str, str] = {}
    for field in ("required_skills", "preferred_skills", "keywords"):
        for t in jd[field]:
            seen.setdefault(normalize(t), t)
    return list(seen.values())


def title_tokens(text: str) -> set:
    return {t for t in re.findall(r"[^\W_]+", normalize(text)) if t not in TITLE_STOPWORDS}


def title_match(resume: Dict[str, Any], jd: Dict[str, Any], m: Matcher) -> Optional[float]:
    if not jd["role"]:
        return None
    titles = [jd["role"]] + jd["role_aliases"]
    head, summ = normalize(resume["headline"]), normalize(resume["summary"])
    if any(m.contains(t, head) for t in titles):
        return TITLE_IN_HEADLINE
    if any(m.contains(t, summ) for t in titles):
        return TITLE_IN_SUMMARY
    role_tokens = title_tokens(jd["role"])
    if not role_tokens:
        return 0.0
    return round(len(role_tokens & title_tokens(resume["headline"])) / len(role_tokens) * TITLE_OVERLAP_MAX, 1)


def date_consistency(resume: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, int]]:
    counts: Dict[str, int] = {}
    scope = "\n".join(resume["sections"].get(k, "") for k in ("experience", "education")) or resume["text"]
    for match in DATE_PATTERN.finditer(scope):
        if match.lastgroup:
            counts[match.lastgroup] = counts.get(match.lastgroup, 0) + 1
    total = sum(counts.values())
    if total < 2:
        return None, counts
    return round(max(counts.values()) / total * 100, 1), counts


def section_completeness(resume: Dict[str, Any]) -> float:
    found = sum(1 for k in SECTION_PATTERNS if resume["sections"].get(k, "").strip())
    return (found + int(resume["has_contact"])) / (len(SECTION_PATTERNS) + 1) * 100


def skills_coverage(resume: Dict[str, Any], jd: Dict[str, Any], m: Matcher, text: str) -> Optional[float]:
    jd_skills = jd["required_skills"] + jd["preferred_skills"]
    if not jd_skills:
        return None  # upstream returns 0; we treat "no skills listed" as not scorable
    skills_text = normalize("\n".join(resume["skills"]))
    matched = sum(1 for s in jd_skills if m.contains(s, skills_text) or m.contains(s, text))
    return min(100.0, matched / len(jd_skills) * 100)


def score(resume_text: str, jd: Dict[str, Any], m: Matcher, master_text: Optional[str] = None) -> Dict[str, Any]:
    resume = parse_resume(resume_text)
    text = normalize(resume_text)
    terms = job_terms(jd)
    matched_terms = {t: m.find(t, text) for t in terms}
    missing = [t for t in terms if matched_terms[t] is None]

    dates_score, date_styles = date_consistency(resume)
    sub = {
        "keyword_match": (len(terms) - len(missing)) / len(terms) * 100,
        "skills_coverage": skills_coverage(resume, jd, m, text),
        "title_match": title_match(resume, jd, m),
        "section_completeness": section_completeness(resume),
        "date_consistency": dates_score,
    }
    scored = {k: v for k, v in sub.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in scored)
    overall = sum(v * WEIGHTS[k] for k, v in scored.items()) / weight if weight else 0.0

    required = {normalize(t) for t in jd["required_skills"]}
    missing_required = [t for t in missing if normalize(t) in required]

    injectable: List[str] = []
    unsupported: List[str] = []
    if master_text is not None:
        mtext = normalize(master_text)
        injectable = [t for t in missing if m.contains(t, mtext)]
        unsupported = [t for t in terms if matched_terms[t] is not None and not m.contains(t, mtext)]

    return {
        "overall_score": round(overall, 1),
        "sub_scores": {k: (round(v, 1) if v is not None else None) for k, v in sub.items()},
        "headline_detected": resume["headline"],
        "matched": {t: v for t, v in matched_terms.items() if v is not None},
        "missing_required": missing_required,
        "missing_other": [t for t in missing if t not in missing_required],
        "injectable_from_master": injectable,
        "unsupported_vs_master": unsupported,
        "date_styles": date_styles,
        "recommendations": recommendations(sub, jd, missing, injectable, unsupported, master_text is not None),
    }


def recommendations(sub, jd, missing, injectable, unsupported, has_master) -> List[str]:
    tips: List[str] = []
    if unsupported:
        tips.append("마스터 이력서에 근거가 없는 공고 키워드가 맞춤 이력서에 있습니다: "
                    + ", ".join(unsupported[:10]) + ". 실제 경험이 아니면 지우고, 사실이면 마스터 이력서에 먼저 추가하세요.")
    if sub["title_match"] is not None and sub["title_match"] < TITLE_IN_HEADLINE:
        tips.append(f"공고 직함 \"{jd['role']}\"을(를) 이력서 첫 줄(헤드라인)에 그대로 쓰는 것을 검토하세요. "
                    "단, 실제로 맡은 역할 범위를 넘는 직함이면 쓰지 않습니다.")
    if injectable:
        tips.append("마스터 이력서에는 있지만 이 버전에서 빠진 키워드: " + ", ".join(injectable[:10]) + ".")
    # Added rule: a missing REQUIRED skill is always reported, whatever the totals say.
    req = {normalize(t) for t in jd["required_skills"]}
    missing_req = [t for t in missing if normalize(t) in req and t not in injectable]
    if missing_req:
        tips.append("필수 요건 누락: " + ", ".join(missing_req)
                    + ". 실제 경험이 있으면 어떤 경력에서 했는지 문장으로 보여주고, 없으면 지원 전에 공고 평가에서 다시 판단하세요.")
    non_injectable = [t for t in missing if t not in injectable]
    if sub["keyword_match"] < 60 and non_injectable:
        tips.append("누락 키워드: " + ", ".join(non_injectable[:5])
                    + ". 실제 경험이 있을 때만 해당 경력 문장에 넣고, 없으면 넣지 않습니다"
                    + ("" if has_master else " (--master로 마스터 이력서를 주면 근거 여부를 자동으로 나눕니다)") + ".")
    if sub["skills_coverage"] is not None and sub["skills_coverage"] < 60:
        tips.append("기술/스킬 섹션에 공고의 도구·기술 중 실제로 쓴 것을 빠짐없이 나열하세요.")
    if sub["section_completeness"] < 100:
        tips.append("연락처와 요약·경력·학력·기술 섹션이 모두 보이게 하세요(섹션 제목: '## 경력', '## Experience' 등).")
    if sub["date_consistency"] is not None and sub["date_consistency"] < 100:
        tips.append("날짜 표기를 한 가지로 통일하세요(예: 2019.03 - 2023.06). 섞이면 ATS가 경력 연수를 잘못 셀 수 있습니다.")
    if sub["keyword_match"] >= 80 and (sub["skills_coverage"] or 0) >= 80:
        tips.append("키워드 정렬이 좋습니다. 성과 문장에 프로필에 있는 수치를 더해 보세요.")
    if not tips:
        tips.append("공고 키워드와 잘 맞습니다. 공고의 자격증·도구 중 빠진 것이 있는지만 확인하세요.")
    return tips


LABELS = {"keyword_match": "키워드 일치 (40%)", "skills_coverage": "스킬 커버리지 (20%)",
          "title_match": "직함 일치 (15%)", "section_completeness": "섹션 구성 (15%)",
          "date_consistency": "날짜 표기 일관성 (10%)"}


def render(r: Dict[str, Any]) -> str:
    out = [f"## 종합 점수: {r['overall_score']} / 100", "", "| 항목 | 점수 |", "|---|---:|"]
    for k, v in r["sub_scores"].items():
        out.append(f"| {LABELS[k]} | {'평가 불가' if v is None else v} |")
    out += ["", f"감지한 헤드라인: {r['headline_detected'] or '(없음)'}", ""]
    if r["missing_required"]:
        out.append("**누락된 필수 요건:** " + ", ".join(r["missing_required"]))
    if r["missing_other"]:
        out.append("**누락된 우대·기타 키워드:** " + ", ".join(r["missing_other"]))
    if r["unsupported_vs_master"]:
        out.append("**⚠ 마스터 이력서에 근거 없음:** " + ", ".join(r["unsupported_vs_master"]))
    out += ["", "### 권장 사항"] + [f"- {t}" for t in r["recommendations"]]
    out += ["", "점수는 키워드 일치 기반 참고값이며 실제 ATS·채용담당자 판단과 다를 수 있습니다."]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="국문·영문 이력서 ↔ 채용공고 키워드 매칭 점수")
    ap.add_argument("resume", help="이력서 Markdown/텍스트 파일")
    ap.add_argument("--jd", required=True, help="공고 키워드 JSON (templates/jd-keywords.example.json)")
    ap.add_argument("--master", help="마스터 이력서 (근거 확인용)")
    ap.add_argument("--synonyms", help="동의어 JSON 경로, 또는 'none'")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        jd = load_jd(args.jd)
        matcher = Matcher(load_synonyms(args.synonyms))
        resume_text = read_text(args.resume)
        master_text = read_text(args.master) if args.master else None
    except (InputError, json.JSONDecodeError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    result = score(resume_text, jd, matcher, master_text)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 1 if result["unsupported_vs_master"] else 0


if __name__ == "__main__":
    sys.exit(main())
