# 원클릭 실행 스크립트

## 파일 3개

| 파일 | 용도 |
|---|---|
| `start_all.ps1` | 7개 서비스 창 + Streamlit 자동 오픈 + 헬스체크 |
| `stop_all.ps1` | 모든 서비스/UI 프로세스 종료 |
| `test_all.ps1` | 헬스체크 → Happy Path → DB 확인 자동 실행 |

## 최초 1회 설정

```powershell
# 프로젝트 루트에서
cd C:\Users\HEMICOLON\Documents\WORK\VIBE\hack\hr-interview-system

# 실행 정책 허용 (한 번만)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 매일 사용법

### 시작
```powershell
.\start_all.ps1
```
→ 8개 창(서비스 7 + Streamlit 1) 자동 오픈, 30초 내 헬스체크 완료

### 테스트
```powershell
.\test_all.ps1
```
→ Happy Path 자동 실행, 결과 표시

### 종료
```powershell
.\stop_all.ps1
```
→ 8개 프로세스 일괄 종료

## 팁

- 서비스 창 제목에 포트번호가 표시됨 (`Version Manager :8001`)
- 특정 서비스만 재시작 → 해당 창에서 Ctrl+C 후 위 화살표로 명령 복원
- Streamlit UI 안 뜨면 `.venv-ui` 생성 여부 확인
