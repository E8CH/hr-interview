# Railway 배포 가이드

**배포 주소: https://hr-interview-production-62ec.up.railway.app**

| 항목 | 값 |
|---|---|
| 프로젝트 | `reliable-rebirth` (`625272fd-bec1-4cf4-830b-1b2ff14ef120`) |
| 서비스 | `hr-interview` |
| 환경 | `production` |
| 연결된 레포 | `E8CH/hr-interview` (`main` 브랜치 push 하면 자동 배포) |
| 볼륨 | `hr-interview-volume` → `/data` (5GB) |

---

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

## 평소 배포

**이미 다 연결돼 있다. `main`에 push 하면 Railway가 알아서 다시 빌드·배포한다.**

```bash
git push origin main
```

진행 상황은 이렇게 본다.

```bash
railway status                       # 배포 상태 (BUILDING/DEPLOYING/SUCCESS/FAILED)
railway logs --build --lines 200     # 빌드가 실패했을 때
railway logs --deployment --lines 200  # 뜬 다음 로그
```

배포가 끝나면 위 주소로 들어간다. 열리는 화면은 로컬 `http://localhost:8501`과
같은 콘솔이다. 이후 사용법은 [README_사용자가이드.md](README_사용자가이드.md)와 동일하다.

---

## 처음부터 다시 만들 때

### 1. 프로젝트 연결

```bash
railway login
railway link --project <프로젝트ID> --environment production
```

또는 웹에서 **New Project → Deploy from GitHub repo**로 `E8CH/hr-interview`를 고른다.
루트에 `Dockerfile`과 `railway.json`이 있으므로 빌더는 자동으로 잡힌다.

### 2. 볼륨 붙이기 (중요)

```bash
railway volume --service <서비스ID> --environment <환경ID> add --mount-path /data
```

`--service`는 **이름이 아니라 ID**를 줘야 한다. 이름을 주면 CLI가 패닉으로 죽는다
(`volume.rs: called Option::unwrap() on a None value`). ID는 `railway status --json`
에서 얻는다. 대시보드에서 **Volume** 추가로 해도 같다.

**이걸 빼면 재배포할 때마다 지원자 명단·시간표가 전부 사라진다.** SQLite 파일은
`/data/db/`에, 업로드된 엑셀은 `/data/storage/<서비스이름>/`에 쌓인다.

### 3. 공개 주소 만들기

```bash
railway domain --service hr-interview
```

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

## 실제 배포에서 확인한 것

첫 배포(`063c1d2`)에서 다음을 확인했다.

- 이미지 빌드 성공 (venv 2개, 리눅스 휠로 `pip install` 완료)
- 서비스 7개 전부 기동. 엔트리포인트가 `7/7 준비 완료 (2초)`를 찍고, 로그에
  `Uvicorn running on http://127.0.0.1:8001`~`8007`과 `Application startup complete.`가
  7개 모두 올라온다. Traceback 없음
- 콘솔이 Railway가 주입한 `$PORT`(8080)에 뜬다
- 공개 주소가 응답한다 — `/_stcore/health` → 200, `/` → 200

배포에서 문제가 나면 Railway 로그의 `[entrypoint]` 줄부터 보면 된다.
어느 서비스가 안 떴는지 `n/7` 형태로 찍는다.

### 처음 배포가 실패했던 이유 (기록)

`Dockerfile`의 `VOLUME ["/data"]` 한 줄 때문에 빌드가 시작조차 못 했다.

```
dockerfile invalid: docker VOLUME at Line 43 is not supported, use Railway Volumes
```

이 에러는 `railway logs --build`에만 찍히고 배포 화면에는 안 보인다.
지시어를 지우고 Railway 볼륨으로 붙여서 해결했다.

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

**주소가 공개돼 있다.** Railway 도메인에는 로그인이 없다. 주소를 아는 사람은
누구나 콘솔에 들어온다. 실제 지원자 데이터를 올릴 거라면 그 전에 접근 제한을
붙여야 한다.

**`--reload`가 없다.** 배포본이므로 코드를 고치면 재배포해야 반영된다.
