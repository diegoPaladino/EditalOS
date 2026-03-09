# RUNBOOK.md — EditalOS

## Ativar ambiente
cd /d C:\Users\diego\Desktop\001-Desktop\programas\ProgramasCriadosPorMim\EditalOS\EditalOS
.venv\Scripts\activate

## Inicializar banco
python -m editalos.cli init-db

## Rodar Streamlit
set PYTHONPATH=%CD%
python -m streamlit run editalos/ui/streamlit_app.py

## Cadastrar disciplina via CLI
python -m editalos.cli add-subject --name "Tecnologia da Informação" --weight 2.0 --question-count 12