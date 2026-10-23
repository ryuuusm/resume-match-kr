# resume-match-kr

국문·영문 이력서를 채용공고에 맞춰 **점검하고, 사실을 지키며 고치는** AI 스킬입니다. Claude Code와 Codex CLI에서 쓰는 `SKILL.md` 형식입니다.

> [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher)(Apache-2.0)의 매칭 점수 방식과 사실 보존 규칙을 한국 이력서용 스킬로 옮긴 파생 작업입니다. Resume-Matcher 프로젝트와 관계없습니다. 자세한 출처는 [NOTICE](NOTICE)를 보세요.

## Resume-Matcher와 달라진 점

| Resume-Matcher | resume-match-kr |
|----------------|-----------------|
| 웹앱 (Next.js + FastAPI + DB, LLM API 키 필요) | **스크립트 1개 + 문서.** 표준 라이브러리만, 설치·네트워크 없음. LLM 작업은 Claude Code/Codex가 담당 |
| JSON 이력서 모델 | **Markdown 이력서**에서 헤드라인·섹션·스킬·연락처를 직접 인식 |
| 영문 섹션 제목, 영문 날짜 | **국문 섹션**(경력·학력·기술·요약)과 **국문 날짜**(2019.03, 2019년 3월) 인식 |
| 한글은 부분 일치만 | **한·영 동의어**(파이썬=Python, 쿠버네티스=Kubernetes), 조사 보정, "자바"가 "자바스크립트" 안에서 잡히지 않게 하는 규칙 |
| 마스터 이력서 대비 "넣을 수 있는 키워드" | 그에 더해 **"근거 없이 들어간 키워드" 검출** (종료 코드 1) |
| 영어 권장 문구 | 한국어 권장 문구. 필수 요건 누락은 점수와 관계없이 항상 표시 |
| - | 국문 이력서·경력기술서, 외국계 영문 resume, 블라인드 채용 규칙 |

점수 가중치는 원본과 같습니다: 키워드 40% · 스킬 20% · 직함 15% · 섹션 15% · 날짜 10%.

## 구성

```
skills/resume-match-kr/
├── SKILL.md                          # 워크플로·체크리스트
├── references/
│   ├── 01-tailoring-rules.md         # 사실 보존 규칙 12개, 수정 강도 4단계
│   ├── 02-resume-structure.md        # 국문·영문 이력서 구조, 날짜 표기
│   └── 03-keyword-extraction.md      # 공고 키워드 JSON 만드는 법
├── scripts/
│   ├── match.py                      # 매칭 점수 + 근거 확인
│   └── _textmatch.py                 # 한·영 키워드 매칭
├── data/synonyms.json                # 한·영 동의어 (수정 가능)
└── templates/
    ├── jd-keywords.example.json
    └── resume.example.md
tests/                                # pytest
```

## 설치

```bash
git clone https://github.com/ryuuusm/resume-match-kr
cp -r resume-match-kr/skills/resume-match-kr ~/.claude/skills/     # Claude Code
cp -r resume-match-kr/skills/resume-match-kr ~/.codex/skills/      # Codex CLI
```

## 사용

1. "이 공고에 맞춰 내 이력서 봐줘" + 공고 본문 + 이력서 → 키워드 추출 → 점수 → 누락 키워드 분류 → 수정안
2. 스크립트만 따로:
   ```bash
   python skills/resume-match-kr/scripts/match.py skills/resume-match-kr/templates/resume.example.md \
          --jd skills/resume-match-kr/templates/jd-keywords.example.json
   ```
   ```
   ## 종합 점수: 84.1 / 100

   | 항목 | 점수 |
   |---|---:|
   | 키워드 일치 (40%) | 76.9 |
   | 스킬 커버리지 (20%) | 66.7 |
   | 직함 일치 (15%) | 100.0 |
   | 섹션 구성 (15%) | 100.0 |
   | 날짜 표기 일관성 (10%) | 100.0 |

   **누락된 필수 요건:** Project Management, Stakeholder Management
   **누락된 우대·기타 키워드:** ITGC
   ```
3. 맞춤본 근거 확인: `match.py tailored.md --jd jd.json --master master.md` → 근거 없는 키워드가 있으면 종료 코드 1

## 주의

- 점수는 키워드 일치 기반 **참고값**입니다. 실제 ATS나 채용담당자의 판단과 다를 수 있습니다.
- PDF/DOCX는 먼저 텍스트로 옮겨야 합니다.
- `master.md` 등 개인 이력서는 이 저장소에 커밋하지 마세요(`.gitignore`).

## 테스트

```bash
pip install pytest
pytest tests -q
```

## 라이선스

Apache License 2.0 ([LICENSE](LICENSE)). Resume-Matcher에서 가져온 부분과 변경 내용은 [NOTICE](NOTICE)와 각 파일 머리말에 적었습니다.

---

### English summary

A Claude Code / Codex skill that scores and tailors Korean and English resumes against a job posting without inventing facts. It ports Resume-Matcher's scoring weights, sub-scores and truthfulness rules into a stdlib-only script plus docs, adding Korean section and date parsing, KO/EN synonyms, a guard against Hangul substring false matches, and a grounding check that flags posting keywords present in a tailored resume but absent from the master resume. Derived from srbhr/Resume-Matcher (Apache-2.0); not affiliated.
