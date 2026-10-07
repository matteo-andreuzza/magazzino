@echo off
rem Avvia la cassa della sagra: aspetta che il server risponda, poi apre l'app in una finestra pulita.
rem Una sola cosa da cambiare: l'indirizzo del computer del programma (se e' questo stesso PC: localhost:8000).
set SERVER=192.168.1.10:8000
set PAGINA=cassa/

title Cassa della sagra
echo Aspetto il server della sagra (http://%SERVER%/) ...
echo Se resta qui per piu' di 3 minuti: il computer del programma e' acceso? Questo PC e' sulla rete della sagra?
:aspetta
curl -s -f -o nul -m 3 http://%SERVER%/salute/ && goto apri
timeout /t 2 /nobreak >nul
goto aspetta

:apri
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
if exist "%CHROME%" (
  start "" "%CHROME%" --app=http://%SERVER%/%PAGINA% --start-maximized
) else (
  start "" "%EDGE%" --app=http://%SERVER%/%PAGINA% --start-maximized
)
