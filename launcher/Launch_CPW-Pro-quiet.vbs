' 静默启动 CPW-Pro（无黑色控制台）；发行根目录的上级即为脚本所在 launcher 的上级。
Set sh = CreateObject("Wscript.Shell")
Dim root : root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(root)
sh.CurrentDirectory = root

exeRel = root & "\myenv\Scripts\pythonw.exe"
If CreateObject("Scripting.FileSystemObject").FileExists(exeRel) Then
  sh.Run Chr(34) & exeRel & Chr(34) & " -m cpwpro", 0, False
Else
  sh.Run "pythonw -m cpwpro", 0, False
End If
