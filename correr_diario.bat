@echo off
REM ====================================================================
REM  Monitoreo diario de precios - se ejecuta solo desde el Programador
REM  de tareas de Windows.
REM
REM  Corre desde ESTE computador y no desde GitHub porque Amazon bloquea
REM  las IP de centros de datos. Una conexion residencial si pasa.
REM ====================================================================

cd /d "D:\Usuario\Downloads\relojes"
set PYTHONIOENCODING=utf-8
set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

echo ================================================
echo  Radar de Relojes  -  %DATE% %TIME%
echo ================================================

REM --- Esperar a que haya internet -------------------------------------
REM Al prender el PC la tarea arranca antes de que el WiFi conecte. Sin esto
REM todas las consultas fallan por falta de red y los precios quedan vacios.
REM Reintenta cada 15 s hasta 20 veces (5 minutos) antes de rendirse.
set INTENTOS=0
:esperar_red
ping -n 1 www.amazon.com >nul 2>&1
if %errorlevel%==0 goto hay_red
set /a INTENTOS+=1
if %INTENTOS% geq 20 (
  echo [!] Sin internet tras 5 minutos. Se cancela; correra en la proxima.
  exit /b 1
)
echo    esperando conexion a internet... intento %INTENTOS%
timeout /t 15 /nobreak >nul
goto esperar_red
:hay_red
echo    internet OK

REM Traer lo que haya cambiado en GitHub (relojes nuevos del formulario)
git pull --rebase --autostash

REM Consultar precios, analizar y enviar alertas por correo
%PY% -m src.main track
if errorlevel 1 (
  echo [!] El monitoreo fallo. No se sube nada.
  exit /b 1
)

REM Publicar historial y dashboard
git add data/prices.db data/watches.csv docs/ reports/
git diff --staged --quiet || git commit -m "precios %DATE%"
git push

echo.
echo Listo. Dashboard: https://crodriguez-relojes.github.io/relojes/
