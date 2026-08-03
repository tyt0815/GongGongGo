Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strPath = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
WshShell.Run Chr(34) & strPath & "\.venv\Scripts\python.exe" & Chr(34) & " main.py", 0, False
