@echo off
setlocal
cd /d "%~dp0\.."
python scripts\init_pdf_knowledge.py %*
