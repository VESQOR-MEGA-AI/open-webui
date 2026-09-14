#!/usr/bin/env bash
# Запуск локальной копии chat.vesqorai.com (ветка vesqor)
# Локальный инстанс на порту 8080. НЕ прод.
cd /opt/projects/vesqor-chat-dev/backend || exit 1
export PATH=/opt/node22/bin:$PATH
exec .venv/bin/python -m uvicorn open_webui.main:app \
  --host 0.0.0.0 --port 8080 \
  --forwarded-allow-ips "*"
