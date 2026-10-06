Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

pythonw = shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python313\pythonw.exe"

If fso.FileExists(pythonw) Then
    shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\web_console_v1\app.py" & Chr(34), 0, False
Else
    shell.Run "cmd /c start " & Chr(34) & Chr(34) & " pythonw " & Chr(34) & root & "\web_console_v1\app.py" & Chr(34), 0, False
End If
