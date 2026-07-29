# HR 면접일정 자동생성 AI Agent — 해커톤 결과 및 도입 제안

해커톤 과제(`HR_면접일정_자동생성_AI_Agent_해커톤문제.pptx`)를 어떻게 풀었는지를
A4 가로 14쪽으로 정리한 제안서다.

| 파일 | 내용 | git |
|---|---|---|
| `HR면접일정_AIAgent_제안서.html` | 문서 본문. 이것 하나가 원본이다 | 올림 |
| `figs/fig1~fig7.png` | 구현 화면 캡처 7장 | **뺌** |
| `HR면접일정_AIAgent_제안서.pdf` | 위 둘을 합쳐 뽑은 인쇄본 | **뺌** |

## 이미지와 PDF 를 왜 뺐나

캡처는 실제 회차 `R20260730-01` 화면이고, 그 회차 데이터는
`docs/희망지원자_*.xlsx` · `docs/취합파일.xlsx` 에서 왔다.
이 파일들은 지원자 467명의 성명·생년월일·성별·국적·병역이 담긴 실데이터라
공개 저장소에 올리지 않기로 했고(`.gitignore` 참고), 그 이름이 그대로 찍힌
캡처와 캡처를 품은 PDF 도 같은 이유로 뺐다.

HTML 본문에는 지원자 이름이 없다. 대신 이미지가 없으면 자리표시자만 보인다.

## 다시 만들기

1. `figs/` 에 화면 캡처 7장을 `fig1-step1-upload.png` … `fig7-hr-timetable.png` 로 넣는다.

   | 파일 | 화면 |
   |---|---|
   | fig1 | 인사 1단계 · 지원자 명단 받기 |
   | fig2 | 인사 2단계 · 팀별 면접 순서 잡기 |
   | fig3 | 인사 3단계 · 각 팀에 명단 보내기 / 가능한 시간 물어보기 |
   | fig4 | 부서 1 · 우리 팀 면접 담당자 정하기 |
   | fig5 | 부서 2 · 면접자 담당자 매칭 |
   | fig6 | 부서 · 담당자별 면접 일정 |
   | fig7 | 인사 4단계 · 면접 시간표 |

   가로:세로 3:2 언저리(1800×1220 정도)로 찍으면 판형에 맞는다.

2. PDF 로 뽑는다. Edge 나 Chrome 의 헤드리스 인쇄를 쓴다.

   ```powershell
   & "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
     --headless=new --disable-gpu --no-pdf-header-footer `
     --run-all-compositor-stages-before-draw --virtual-time-budget=8000 `
     --print-to-pdf="$PWD\HR면접일정_AIAgent_제안서.pdf" `
     "file:///$($PWD -replace '\\','/')/HR면접일정_AIAgent_제안서.html"
   ```

   브라우저에서 열어 인쇄해도 된다 — **A4 · 가로 · 여백 없음 · 배경 그래픽 켜기**.

## 숫자 출처

본문의 수치는 전부 이 저장소에서 잰 값이다. 지어낸 값은 없다.

| 값 | 잰 방법 |
|---|---|
| 자동 시험 787개 (01=44 · 02=104 · 03=176 · 04=133 · 05=82 · 06=129 · 07=119) | 서비스별 `pytest --collect-only` |
| 커버리지 91~97% | 서비스별 `pytest --cov` |
| REST 엔드포인트 86개 | 라우터의 경로 데코레이터 집계 |
| 파이썬 38,528줄 | `services/`·`shared/`·`tools/`·`bff/` 의 `.py` 줄 수 |
| 배정 연산 2.72ms · 배치율 100% · HARD 위반 0건 · 규칙 준수율 100.0 | 04-scheduler 시간표 생성 API 응답(`elapsed_ms`, `coverage_pct`, `hard_violations`, `rule_compliance`) |
| 화면 왕복 약 1.0초 | 콘솔에서 [시간표 만들기] 누른 뒤 결과 표시까지 |
| 회차 R20260730-01 · 5개 팀 · 지원자 83명 · 담당자 50명 | 해당 회차 조회 결과 |

현행 업무 규모(주 1~2회, 회당 500명 이상, 16~48시간, 5단계)와 KPI 목표는
과제 정의서에서 가져온 값이라 본문에도 출처를 밝혀 두었다.
