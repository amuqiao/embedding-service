#!/bin/sh
# Taskiq worker liveness probe — K8s livenessProbe.exec 入口
# 使用方式：exec 此脚本，退出码 0 表示健康，非 0 触发 K8s 重启。
# K8s 配置示例：
#   livenessProbe:
#     exec:
#       command: ["/app/check-worker-health.sh"]
#     initialDelaySeconds: 30
#     periodSeconds: 60
#     failureThreshold: 3
ps -eo command | grep -F "taskiq worker app.tasks.taskiq_app:broker" | grep -v grep >/dev/null
