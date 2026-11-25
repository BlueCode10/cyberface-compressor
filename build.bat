@echo off
rem Build the Cyberface Compressor: needs Python 3 with numpy, zopfli and pyinstaller
rem   python -m pip install numpy zopfli pyinstaller
cd /d "%~dp0"
python -c "import zopfli" 2>nul || (echo zopfli is missing: python -m pip install zopfli & exit /b 1)
python -m PyInstaller CyberfaceCompressor.spec --noconfirm --distpath dist --workpath build || exit /b 1
rem put CyberfaceCompressor.exe and _internal next to this script
if exist _internal rmdir /s /q _internal
xcopy /e /i /q /y "dist\Cyberface Compressor" . >nul
echo Built: %~dp0CyberfaceCompressor.exe
