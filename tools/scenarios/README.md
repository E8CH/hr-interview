# tools/scenarios/

재사용 가능한 시나리오 스크립트를 여기에 추가하세요.

## 기본 시나리오

- `test_runner.py scenario happy` — Happy Path (마스터 → 배포 → 스케줄 → KPI)
- `test_runner.py scenario noshow` — 노쇼 재편성

## 커스텀 시나리오 추가 방법

1. 이 폴더에 `custom_XXX.py` 생성
2. `test_runner.py`의 `scenario` 명령에 등록
