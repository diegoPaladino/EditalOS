# PROJECT_STATUS.md — EditalOS

## Caminho local
C:\Users\diego\Desktop\001-Desktop\programas\ProgramasCriadosPorMim\EditalOS\EditalOS

## Estado atual validado
- `.venv` criado e funcionando
- dependências instaladas por `requirements.txt`
- banco SQLite criado: `editalos.db`
- comando validado:
  - `python -m editalos.cli init-db`
- disciplina de teste cadastrada:
  - Tecnologia da Informação
- Streamlit funcionando com:
  - `set PYTHONPATH=%CD%`
  - `python -m streamlit run editalos/ui/streamlit_app.py`

## Problemas já resolvidos
- erro de módulo `editalos` não encontrado
- erro de `sqlalchemy` ausente por uso do Python global
- erro de `default_factory` em dataclass no planner

## Situação da interface
- dashboard abre
- exibe contadores e plano diário básico
- ainda não possui:
  - formulários
  - botões de cadastro
  - logs de biohacking
  - start/pause/stop de sessão

## Próximo objetivo
Implementar interface Streamlit operacional começando por:
1. cadastro de disciplina
2. cadastro de tópico

## Restrições
- tudo local
- Windows
- foco atual em robustez, não em embalagem `.exe`