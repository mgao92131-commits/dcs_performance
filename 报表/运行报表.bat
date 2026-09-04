@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo 月度考核报表
echo.
echo 1. 快速更新上一个班
echo 2. 更新指定日期和班组
echo 3. 更新日期范围
echo 4. 设置免考
echo 5. 强制重算指定班次
echo 6. 同步规则
echo 7. 新建月份
echo 8. 快速更新上一个班并发送邮件
echo 0. 退出
echo.
set /p choice=请选择：

if "%choice%"=="1" py -3 "报表\report.py" update --last
if "%choice%"=="2" goto specified
if "%choice%"=="3" goto daterange
if "%choice%"=="4" goto exempt
if "%choice%"=="5" goto force
if "%choice%"=="6" py -3 "报表\report.py" sync-rules
if "%choice%"=="7" goto newmonth
if "%choice%"=="8" py -3 "报表\report.py" update --last --send-email
if "%choice%"=="0" exit /b 0
goto done

:specified
set /p report_date=日期（YYYY-MM-DD）：
set /p report_team=班组（甲/乙/丙）：
py -3 "报表\report.py" update --date "%report_date%" --team "%report_team%"
goto done

:daterange
set /p date_from=开始日期（YYYY-MM-DD）：
set /p date_to=结束日期（YYYY-MM-DD）：
py -3 "报表\report.py" update --from "%date_from%" --to "%date_to%"
goto done

:exempt
set /p exempt_date=日期（YYYY-MM-DD）：
set /p exempt_team=班组（甲/乙/丙）：
py -3 "报表\report.py" exempt --date "%exempt_date%" --team "%exempt_team%"
goto done

:force
set /p force_date=日期（YYYY-MM-DD）：
set /p force_team=班组（甲/乙/丙）：
py -3 "报表\report.py" update --date "%force_date%" --team "%force_team%" --force
goto done

:newmonth
set /p report_month=月份（YYYY-MM）：
py -3 "报表\report.py" new-month --month "%report_month%"

:done
echo.
pause
endlocal
