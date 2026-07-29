# 🎛️ HR 통합 테스트 콘솔 — 설치 & 사용법

7개 마이크로서비스를 한 곳에서 조작하는 개발자 콘솔입니다.

---

## 📦 압축 파일 내용

```
tools/
├── test_runner.py         ← CLI + 대화형 메뉴 (핵심)
├── test_console.py        ← Streamlit 브라우저 UI
├── scenarios/             ← 시나리오 확장 위치
│   └── README.md
└── fixtures/              ← 샘플 파일 (여기에 master_sample.xlsx 배치)
    └── README.md
README.md                  ← 이 파일
```

---

## 🚀 설치 (3단계)

### 1. 압축 풀기
프로젝트 루트(`hr-interview-system`)에서 이 ZIP을 압축 해제합니다.

```powershell
cd C:\Users\HEMICOLON\Documents\WORK\VIBE\hack\hr-interview-system

# 다운로드한 hr_test_tools.zip을 이 폴더에서 압축 풀기
Expand-Archive -Path .\hr_test_tools.zip -DestinationPath .
```

압축 해제 후 폴더에 `tools/` 가 추가됩니다.

### 2. 의존성 설치
venv 활성화한 상태에서:

```powershell
.\.venv\Scripts\Activate.ps1
pip install httpx streamlit
```

`httpx`는 이미 설치돼 있을 수 있음 (문제없음).

### 3. 샘플 파일 배치
Happy Path 시나리오가 참조하는 파일을 `tools/fixtures/`에 배치합니다.

```powershell
# 예: 실제 취합파일 복사
copy path\to\취합파일.xlsx tools\fixtures\master_sample.xlsx
```

---

## 🎯 사용법

### CLI 명령어 (venv 활성화 상태에서)

```powershell
# 7개 서비스 헬스체크
python tools\test_runner.py health

# 대화형 메뉴 (초보자 친화적)
python tools\test_runner.py menu

# Happy Path 자동 실행
python tools\test_runner.py scenario happy

# 노쇼 재편성 시나리오
python tools\test_runner.py scenario noshow

# DB 상태 요약
python tools\test_runner.py db

# 이벤트 타임라인 조회
python tools\test_runner.py events                # 전체
python tools\test_runner.py events R2026-TEST-01  # 특정 회차

# Swagger UI 링크 목록
python tools\test_runner.py docs

# SQLite 초기화 (모든 데이터 삭제)
python tools\test_runner.py reset
```

### Streamlit 브라우저 콘솔

```powershell
streamlit run tools\test_console.py
```

자동으로 브라우저에 `http://localhost:8501` 열림.

**탭 구성**
- **헬스체크**: 7개 서비스 상태를 카드로 표시
- **시나리오 실행**: 마스터 파일 업로드 후 자동 실행, 각 단계 결과 실시간
- **이벤트 타임라인**: Service 07의 감사 이벤트 조회
- **DB 상태**: 각 서비스 SQLite 테이블별 행 수
- **링크 · 문서**: 7개 서비스의 Swagger UI 링크

---

## 🎬 시연 시나리오 (Service 08 없이)

발표 · 데모 시 다음 흐름 권장:

1. **CLI 헬스체크로 인프라 준비 시연**
   ```powershell
   python tools\test_runner.py health
   ```

2. **Streamlit 콘솔 열기**
   ```powershell
   streamlit run tools\test_console.py
   ```

3. **"시나리오 실행" 탭에서 마스터 업로드 → 자동 진행 시연**
   - 각 단계별 API 호출 및 응답 실시간 표시
   - 대시보드 KPI 자동 렌더링

4. **"이벤트 타임라인" 탭에서 감사 흐름 시연**
   - 모든 이벤트가 순서대로 저장됨을 시연

5. **Swagger UI 링크로 개별 API 상세 시연**

---

## 🔧 자주 발생하는 이슈

**httpx 미설치**
```
❌ httpx 미설치: pip install httpx
```
→ `pip install httpx`

**Streamlit 미설치**
```
ModuleNotFoundError: No module named 'streamlit'
```
→ `pip install streamlit`

**서비스가 DOWN으로 표시됨**
→ 해당 서비스 창에서 `uvicorn app.main:app --port 800X --reload` 실행 중인지 확인

**샘플 파일 없음**
```
❌ 샘플 파일 없음: tools/fixtures/master_sample.xlsx
```
→ `tools/fixtures/README.md` 참조하여 파일 배치

**한글 인코딩 문제 (Windows)**
```powershell
# PowerShell UTF-8 강제
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001
```

---

## 💡 개발 흐름에서 언제 쓰는가

**아침 시작 시**
- 각 창에서 uvicorn 실행 후 `python tools\test_runner.py health` 로 전체 상태 확인

**API 하나 완성 시**
- Swagger UI (`http://localhost:800X/docs`) 로 개별 API 검증

**저녁 통합 검증**
- `python tools\test_runner.py scenario happy` 로 전체 흐름 자동 검증

**문제 발생 시**
- `python tools\test_runner.py events <round_id>` 로 어느 단계에서 막혔는지 추적
- `python tools\test_runner.py db` 로 각 DB에 데이터가 쌓였는지 확인

**시연 · 발표**
- `streamlit run tools\test_console.py` 로 시각적 데모

---

## 📌 최종 확인 체크리스트

- [ ] `tools/` 폴더가 프로젝트 루트에 있음
- [ ] venv 활성화 상태에서 `pip install httpx streamlit` 완료
- [ ] `tools/fixtures/master_sample.xlsx` 배치 완료
- [ ] `python tools/test_runner.py health` 정상 실행
- [ ] 각 서비스 창에서 uvicorn 실행 중일 때 헬스체크 초록불
