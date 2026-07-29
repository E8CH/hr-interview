FROM python:3.11-slim

WORKDIR /srv

COPY services/03-response-collector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared/contracts 는 읽기 전용 공통 계약 — app 이 sys.path 로 참조한다
COPY shared ./shared
COPY services/03-response-collector/app ./services/03-response-collector/app
COPY services/03-response-collector/scripts ./services/03-response-collector/scripts

WORKDIR /srv/services/03-response-collector
EXPOSE 8003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
