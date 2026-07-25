@echo off
set "JAVA_HOME=C:\Program Files (x86)\Android\openjdk\jdk-17.0.8.101-hotspot"
set "ANDROID_HOME=C:\Program Files (x86)\Android\android-sdk"
cd /d "%~dp0android"
call gradlew.bat assembleRelease
