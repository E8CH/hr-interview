#!/bin/bash
# 7개 서비스 헬스체크
for port in 8001 8002 8003 8004 8005 8006 8007; do
  if curl -s -f http://localhost:$port/healthz > /dev/null; then
    echo "✅ port $port OK"
  else
    echo "❌ port $port DOWN"
  fi
done
