@echo off
chcp 65001 >nul
REM 已升级：支持完整版/精简版双模式
REM 直接双击项目根目录的「一键打包.bat」选择模式即可。
call "%~dp0..\一键打包.bat"
