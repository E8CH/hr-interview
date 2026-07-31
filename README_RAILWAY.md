# Railway 배포 가이드

서비스 7개와 Streamlit 콘솔을 **한 컨테이너**에 담아 Railway 서비스 1개로 띄운다.

로컬(`start_all.ps1`)과 같은 모양을 유지하는 것이 이 구성의 목적이다.
서비스는 컨테이너 안에서 그대로 `127.0.0.1:8001~8007`에 뜨고, 콘솔이 쓰는
주소가 바뀌지 않는다. 그래서 애플리케이션 코드는 **한 줄도 고치지 않았다.**

---

## 파일

| 파일 | 역할 |
|---|---|
| `Dockerfile` | venv 2개(서비스용·콘솔용)를 만들고 레포 구조를 그대로 담는다 |
| `docker/start-all.sh` | 서비스 7개를 백그라운드로, 콘솔을 포그라운드로 띄운다 |
| `requirements-ui.txt` | 콘솔 전용 의존성 (로컬 `.venv-ui`에 해당) |
| `railway.json` | 빌더·헬스체크·재시작 정책 |
| `.dockerignore` | 개인정보 실데이터가 이미지에 들어가지 않게 막는다 |

---

## 순서

### 1. Railway 프로젝트 만들기

```bash
railway login
railway init
railway link      # 기존 프로젝트에 붙일 때
```

또는 웹에서 **New Project → Deploy from GitHub repo**로 `E8CH/hr-interview`를 고른다.
루트에 `Dockerfile`과 `railway.json`이 있으므로 빌더는 자동으로 잡힌다.

### 2. 볼륨 붙이기 (중요)

Railway 대시보드에서 서비스에 **Volume**을 추가하고 마운트 경로를 이렇게 준다.

```
/data
```

**이걸 빼면 재배포할 때마다 지원자 명단·시간표가 전부 사라진다.** SQLite 파일은
`/data/db/`에, 업로드된 엑셀은 `/data/storage/<서비스이름>/`에 쌓인다.

### 3. 배포

```bash
railway up
```

빌드가 끝나면 **Settings → Networking → Generate Domain**으로 공개 주소를 만든다.
열리는 화면은 로컬 `http://localhost:8501`과 같은 콘솔이다.

이후 사용법은 [README_사용자가이드.md](README_사용자가이드.md)와 동일하다.

---

## 환경변수

**아무것도 설정하지 않아도 뜬다.** 엔트리포인트가 `DATABASE_URL`과 `STORAGE_DIR`만
서비스별로 넣어 주고, 나머지는 코드의 기본값을 그대로 쓴다. 그래서 `USE_MOCK=true`,
`REDIS_URL=fakeredis://` 같은 로컬 기본 동작이 유지된다.

바꾸고 싶을 때만 Railway 변수에 넣는다.

| 변수 | 기본값 | 언제 바꾸나 |
|---|---|---|
| `PORT` | Railway가 자동 주입 | 건드리지 말 것 |
| `DATA_DIR` | `/data` | 볼륨 마운트 경로를 다르게 줬을 때 |
| `USE_MOCK` | `true` | 합성 데이터 대신 실제 연동을 붙일 때 |
| `REDIS_URL` | `fakeredis://` | 실제 Redis를 붙일 때 |
| `FORM_BASE_URL` | `http://localhost:8003` | **아래 "알려진 제약" 참고** |

---

## 확인한 것과 확인하지 못한 것

작업한 PC에 Docker가 없어서 **컨테이너를 실제로 빌드·구동해 보지는 못했다.**
대신 컨테이너와 같은 조건을 로컬에서 재현해 다음을 확인했다.

확인된 것

- 서비스 7개 전부 컨테이너와 같은 환경변수(절대경로 `DATABASE_URL`, 서비스별
  `STORAGE_DIR`)로 startup을 통과하고 `/healthz`가 200을 준다
- 그 경로에 SQLite 파일과 테이블이 실제로 만들어진다
- `shared/` import가 7개 서비스 모두에서 성공한다 (감사 이벤트 포워딩이 살아 있다)
- 콘솔이 엔트리포인트와 같은 streamlit 옵션으로 뜨고, Railway 헬스체크 경로인
  `/_stcore/health`가 200을 준다
- 서비스용·콘솔용 의존성이 각자 충돌 없이 해결된다

확인하지 못한 것

- **리눅스에서의 첫 구동.** 지금까지 Windows에서만 돌던 코드다. 파일명 대소문자
  구분처럼 리눅스에서만 드러나는 문제가 남아 있을 수 있다
- 이미지 빌드 자체 (`pip install`이 리눅스 휠로 잘 끝나는지)

첫 배포에서 문제가 나면 Railway 로그의 `[entrypoint]` 줄부터 보면 된다.
어느 서비스가 안 떴는지 `n/7` 형태로 찍는다.

---

## 알려진 제약

**면접위원에게 나가는 폼 주소가 `localhost`다.**
Service 03의 `FORM_BASE_URL` 기본값이 `http://localhost:8003`이다. 로컬에서는
맞지만 배포 환경에서는 외부 사람이 열 수 없는 주소다. 현재 구성은 콘솔 하나만
외부로 노출하므로, 실제로 외부 발송까지 시연하려면 8003을 따로 노출하고 이 값을
바꿔야 한다. 콘솔 안에서 진행하는 시연에는 영향이 없다.

**개인정보 실데이터는 이미지에 들어가지 않는다.**
`.dockerignore`가 `docs/`, `services/*/data/`, `**/storage/`, 모든 `*.xlsx`를
막는다(합성 샘플인 `tools/fixtures/*.xlsx`만 예외). 이건 `.gitignore`와 같은
정책이다. 실데이터가 빠지면 각 서비스는 `USE_MOCK=true` 기본값으로 합성 데이터를
만들어 쓰므로 시연에 지장이 없다.

**서비스별 로그가 한 스트림에 섞인다.**
로컬은 창 8개로 나뉘어 보이지만 여기서는 한 곳에 모인다.

**`--reload`가 없다.** 배포본이므로 코드를 고치면 재배포해야 반영된다.
