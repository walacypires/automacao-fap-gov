@echo off
setlocal

rem Porta do remote debugging (chromium DevTools)
set PORT=9222

rem Perfil do Brave a usar (o mesmo que você usa manualmente)
set PROFILE=Pessoal

rem Diretório de dados do Brave
set USERDATA=%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data

rem Regras de pinagem de IP (host_resolver_rules)
set RULES=MAP sso.acesso.gov.br 161.148.168.40,MAP fap.dataprev.gov.br 200.152.35.17,EXCLUDE localhost

rem Caminho do Brave (x64 e x86)
set BRAVE="C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
if not exist %BRAVE% set BRAVE="C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"

echo Iniciando Brave com DevTools em %PORT% e pinagem de IP...
start "" %BRAVE% ^
  --remote-debugging-port=%PORT% ^
  --user-data-dir="%USERDATA%" ^
  --profile-directory="%PROFILE%" ^
  --host-resolver-rules="%RULES%" ^
  --disable-popup-blocking

endlocal