#!/usr/bin/env bash
# Установка питон-зависимостей open-webui (ветка vesqor) для локальной копии chat.vesqorai.com
set -uo pipefail
cd /opt/projects/vesqor-chat-dev/backend

echo "=== $(date) СТАРТ установки ==="
df -h / | tail -1

echo "=== Шаг 1: torch CPU (~200 МБ скачать, вместо CUDA ~2.5 ГБ) ==="
.venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -5
echo "Step1 exit: $?"

echo "=== Шаг 2: полный requirements.txt ==="
.venv/bin/pip install --no-cache-dir -r requirements.txt 2>&1 | tail -25
echo "Step2 exit: $?"

echo "=== $(date) ГОТОВО ==="
df -h / | tail -1
du -sh /opt/projects/vesqor-chat-dev/backend/.venv
