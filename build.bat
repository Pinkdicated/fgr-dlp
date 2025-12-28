@echo off
python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --enable-plugin=pyqt6 ^
    --output-dir=dist ^
    main.py

pause