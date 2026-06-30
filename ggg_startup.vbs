Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strPath = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath

' 1. 서버 실행 (배치 파일 이름이 run_app.bat 인지 확인하세요)
WshShell.Run "cmd.exe /c ggg_startup.bat", 0, False

' 2. 로그 파일이 생성될 시간을 충분히 줌 (2초로 늘림)
WScript.Sleep 2000

' 3. 로그 창 실행 (절대 경로를 사용하여 더 안전하게 읽기)
' 여기서 'server.log' 앞에 '.\'를 붙이거나 현재 경로 변수를 활용합니다.
WshShell.Run "powershell.exe -NoExit -Command ""chcp 65001; Get-Content -Path 'server.log' -Wait -Encoding utf8""", 1, False