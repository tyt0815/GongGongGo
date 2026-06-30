@echo off

cd /d %~dp0

if exist server.log del server.log

set PYTHONIOENCODING=utf-8
chcp 65001 > nul

start "GGG" /B c:\Users\geun\Documents\1.Project\GongGongGo\.venv\Scripts\python.exe -u c:/Users/geun/Documents/1.Project/GongGongGo/main.py >> c:/Users/geun/Documents/1.Project/GongGongGo/server.log 2>&1