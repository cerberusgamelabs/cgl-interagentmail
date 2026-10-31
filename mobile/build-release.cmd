@echo off
setlocal
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"
if not defined ANDROID_HOME set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
cd /d "%~dp0"
call npm.cmd run sync
if errorlevel 1 exit /b 1
cd android
call gradlew.bat --no-daemon --max-workers=2 :app:testReleaseUnitTest :app:assembleRelease :app:lintRelease --console=plain
exit /b %errorlevel%
