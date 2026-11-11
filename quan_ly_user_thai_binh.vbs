Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Uu tien pythonw trong PATH hoac LocalAppData
Dim pythonPath
pythonPath = "pythonw.exe"

On Error Resume Next
WshShell.Run "pythonw.exe QuanLyUser.py thai_binh", 0, False
If Err.Number <> 0 Then
    Err.Clear
    Dim localPython
    localPython = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe"
    WshShell.Run """" & localPython & """ QuanLyUser.py thai_binh", 0, False
End If
