# 빌드 컨텍스트는 리포지토리 루트다 (shared/contracts 를 함께 넣어야 하므로).
#   docker build -f services/05-repair-engine/Dockerfile -t repair-engine .
FROM python:3.11-slim

WORKDIR /app
COPY services/05-repair-engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/05-repair-engine/app ./app
EXPOSE 8005

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
