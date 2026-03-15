@echo off
cd /d "C:\Users\diego\Desktop\001-Desktop\programas\ProgramasCriadosPorMim\EditalOS\EditalOS"
set PYTHONPATH=%CD%
".\.venv\Scripts\python.exe" -m streamlit run ".\editalos\ui\streamlit_app.py"
