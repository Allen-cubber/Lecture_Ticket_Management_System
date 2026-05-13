@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git was not found. Please install Git first.
  pause
  exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo [ERROR] This folder is not a Git repository.
  pause
  exit /b 1
)

for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if "%BRANCH%"=="" set "BRANCH=main"

echo Repository: %CD%
echo Branch: %BRANCH%
echo.
echo Current changes:
git status --short
echo.

set "COMMIT_MSG=%~1"
if "%COMMIT_MSG%"=="" (
  set /p "COMMIT_MSG=Commit message (press Enter for auto message): "
)
if "%COMMIT_MSG%"=="" (
  set "COMMIT_MSG=Update project"
)

echo.
echo [1/4] Staging all changes...
git add -A
if errorlevel 1 goto fail

git diff --cached --quiet
if errorlevel 1 (
  echo [2/4] Committing changes...
  git commit -m "%COMMIT_MSG%"
  if errorlevel 1 goto fail
) else (
  echo [2/4] No local changes to commit.
)

echo [3/4] Pulling latest remote changes with rebase...
git pull --rebase origin "%BRANCH%"
if errorlevel 1 goto fail

echo [4/4] Pushing to GitHub...
git push -u origin "%BRANCH%"
if errorlevel 1 goto fail

echo.
echo [OK] Push completed successfully.
pause
exit /b 0

:fail
echo.
echo [ERROR] Push failed. Please read the message above.
echo If there is a rebase conflict, resolve it, then run:
echo   git rebase --continue
echo   git push -u origin %BRANCH%
pause
exit /b 1
