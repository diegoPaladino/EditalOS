# RUNBOOK.md — EditalOS

## Ativar ambiente
cd /d C:\Users\diego\Desktop\001-Desktop\programas\ProgramasCriadosPorMim\EditalOS\EditalOS
.venv\Scripts\activate

## Inicializar banco
python -m editalos.cli init-db

## Rodar Streamlit
set PYTHONPATH=%CD%
python -m streamlit run editalos/ui/streamlit_app.py

## Importar estrategia ANKI
python -m editalos.cli import-strategy --path "C:\caminho\Estrutura_Anki.txt" --name "SEFAZ - Estrategia ANKI"

## Exportar cards legados para ANKI
python -m editalos.cli export-anki --path "anki_export_editalos.tsv"

## Cadastrar disciplina via CLI
python -m editalos.cli add-subject --name "Tecnologia da Informação" --weight 2.0 --question-count 12
