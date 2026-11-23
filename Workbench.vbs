Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
base = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(base, ".venv\Scripts\python.exe")
stamp = files.BuildPath(base, ".venv\workbench-requirements.txt")
lock = files.BuildPath(base, "requirements.lock")
ready = files.FileExists(python) And files.FileExists(stamp) And files.FileExists(lock)
If ready Then ready = (files.OpenTextFile(stamp).ReadAll = files.OpenTextFile(lock).ReadAll)
If ready Then ready = (shell.Run(Chr(34) & python & Chr(34) & " -c ""import pip""", 0, True) = 0)
If Not ready Then
  shell.Run Chr(34) & files.BuildPath(base, "Launch Workbench.cmd") & Chr(34), 1, False
  WScript.Quit 0
End If
code = shell.Run(Chr(34) & files.BuildPath(base, "Run.cmd") & Chr(34) & " desktop_app.py", 0, True)
If code <> 0 Then
  MsgBox "Agent Workbench stopped with an error (code " & code & ")." & vbCrLf & "Double-click Launch Workbench.cmd in " & base & " to see the message.", 48, "Agent Workbench"
End If
