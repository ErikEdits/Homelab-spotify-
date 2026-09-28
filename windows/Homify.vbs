' Startet Homify ohne Konsolenfenster und oeffnet das Programmfenster.
' Aufruf mit "background" (Autostart): nur den Server starten, kein Fenster.
Option Explicit
Dim fso, sh, root, pyw, args, env

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pyw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pyw) Then
  MsgBox "Homify ist noch nicht installiert." & vbCrLf & _
         "Bitte zuerst windows\Installieren.bat ausfuehren.", 48, "Homify"
  WScript.Quit 1
End If

args = "-m homify run --open"
If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "background" Then args = "-m homify run"
End If

Set env = sh.Environment("Process")
env("PYTHONUTF8") = "1"
sh.CurrentDirectory = root
sh.Run """" & pyw & """ " & args, 0, False
