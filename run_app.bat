@echo off

cd /d %~dp0

if exist server.log del server.log

set PYTHONIOENCODING=utf-8
chcp 65001 > nul

start "GGG" /B C:\Users\582\miniconda3\envs\ggg\python.exe -u main.py >> server.log 2>&1