#!/bin/bash
# 로컬 전체 기동
set -e
cd "$(dirname "$0")/../infra"
docker-compose up --build -d
sleep 5
cd - > /dev/null
bash scripts/health_check_all.sh
