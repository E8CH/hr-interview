# 서비스 7개 + Streamlit 콘솔을 한 이미지에 담는다 (Railway 서비스 1개로 배포).
#
# 왜 venv 를 두 개 만드는가
#   fastapi 0.115.0 은 starlette<0.39 를, streamlit 1.60.0 은 starlette>=0.40 을
#   요구해서 한 환경에 못 담는다. 로컬이 .venv / .venv-ui 로 나눠 쓰는 이유가
#   이것이고, 여기서도 같은 구조를 그대로 따른다.
#
# 왜 레포 디렉터리 구조를 유지하는가
#   각 서비스의 app/__init__.py 가 `Path(__file__).resolve().parents[3]` 로 레포
#   루트를 찾아 shared/ 를 sys.path 에 넣는다. services/<이름>/app/ 깊이가 바뀌면
#   감사 이벤트 포워딩(shared.contracts.audit_sink)이 조용히 꺼진다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ROOT=/srv \
    DATA_DIR=/data \
    SVC_PY=/opt/venv-svc/bin/python \
    UI_PY=/opt/venv-ui/bin/python

WORKDIR /srv

# 의존성 먼저 — 코드가 바뀌어도 이 레이어는 캐시된다
COPY requirements.txt requirements-ui.txt ./
RUN python -m venv /opt/venv-svc \
 && python -m venv /opt/venv-ui \
 && /opt/venv-svc/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv-ui/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv-svc/bin/pip install --no-cache-dir -r requirements.txt \
 && /opt/venv-ui/bin/pip install --no-cache-dir -r requirements-ui.txt

# 레포 레이아웃 그대로 (경로 깊이가 의미를 가진다)
COPY shared ./shared
COPY services ./services
COPY tools ./tools
COPY .streamlit ./.streamlit
COPY docker/start-all.sh /usr/local/bin/start-all.sh
RUN chmod +x /usr/local/bin/start-all.sh

# DB·업로드 파일 자리. Railway 볼륨을 여기에 붙인다.
# VOLUME 지시어는 쓰지 않는다 — Railway 빌더가 거부한다
# ("docker VOLUME is not supported, use Railway Volumes").
# 마운트는 Railway 쪽에서 /data 로 걸고, 여기서는 디렉터리만 만들어 둔다.
RUN mkdir -p /data/db /data/storage

# 외부로 나가는 것은 콘솔 하나뿐. 8001~8007 은 컨테이너 내부 전용이다.
EXPOSE 8501

CMD ["/usr/local/bin/start-all.sh"]
