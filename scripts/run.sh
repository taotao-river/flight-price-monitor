#!/bin/bash
cd "$(dirname "$0")" || exit 1
# 错开整点，避开被限流的高峰
sleep $((RANDOM % 180))
exec ./.venv/bin/python monitor.py
