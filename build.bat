@echo off
echo ========================================
echo FitGirl Repacks Indirici - EXE Build
echo ========================================
echo.

REM Nuitka'nin yuklu oldugunu kontrol et
python -c "import nuitka" 2>nul
if errorlevel 1 (
    echo [HATA] Nuitka bulunamadi!
    echo Lutfen su komutu calistirin: pip install nuitka
    pause
    exit /b 1
)

echo [1/4] Nuitka ile EXE olusturuluyor...
echo.

REM Nuitka ile build
python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=pyqt6 ^
    --include-package-data=PyQt6 ^
    --include-package-data=libtorrent ^
    --include-data-dir=libtorrent-windows-dll=libtorrent-windows-dll ^
    --output-dir=dist ^
    --output-filename=FitGirl-Repacks-Indirici.exe ^
    --remove-output ^
    --assume-yes-for-downloads ^
    --show-progress ^
    --show-memory ^
    main.py

if errorlevel 1 (
    echo.
    echo [HATA] Build basarisiz oldu!
    pause
    exit /b 1
)

echo.
echo [2/4] EXE olusturuldu: dist\FitGirl-Repacks-Indirici.exe
echo.

REM DLL'leri kontrol et
if exist "dist\FitGirl-Repacks-Indirici.dist\*.dll" (
    echo [3/4] DLL dosyalari bulundu
) else (
    echo [UYARI] DLL dosyalari bulunamadi, libtorrent calismayabilir
)

echo.
echo [4/4] Build tamamlandi!
echo.
echo EXE dosyasi: dist\FitGirl-Repacks-Indirici.exe
echo.
echo NOT: Eger libtorrent DLL hatasi alirsaniz:
echo   - Visual C++ Redistributable yukleyin
echo   - libtorrent-windows-dll paketinin DLL'lerini EXE ile ayni klasore kopyalayin
echo.
pause

