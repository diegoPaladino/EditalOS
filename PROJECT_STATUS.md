# PROJECT_STATUS.md - EditalOS

## Caminho local
C:\Users\diego\Desktop\001-Desktop\programas\ProgramasCriadosPorMim\EditalOS\EditalOS

## Atualizacao deste status
- Data de atualizacao: 2026-03-15
- Base: leitura de `AGENTS.md`, `PROJECT_STATUS.md`, codigo atual, implementacao da primeira interface de flashcards no Streamlit com revisao FSRS/SM-2, correcao de datetime naive/aware na execucao de sessoes e contexto detalhado de revisao por sessao.

## Estado atual real do projeto
- Estrutura principal em pacote `editalos` com camadas de CLI, services e UI Streamlit.
- Banco SQLite local `editalos.db` presente e inicializavel via `python -m editalos.cli init-db`.
- Streamlit em `editalos/ui/streamlit_app.py` agora esta orientado a execucao diaria (nao apenas cadastro/dashboard).
- Streamlit agora possui secao operacional de flashcards com criacao manual por topico, fila de revisao e uso do motor SRS ja existente.
- Servico de execucao de estudo agora normaliza datetimes naive/aware para UTC antes de calcular tempo bruto/liquido, evitando quebra ao retomar sessoes ativas lidas do SQLite.
- Caminho padrao do banco SQLite agora e absoluto para evitar abrir instancias apontando para arquivos `editalos.db` diferentes conforme o diretorio de execucao.
- Engine SQLite ajustada para uso local com Streamlit sem `QueuePool`, reduzindo risco de `sqlalchemy.exc.TimeoutError` em reruns/conexoes concorrentes.
- Inicializacao de schema no Streamlit agora e cacheada por processo, evitando repetir `create_all()` a cada rerun da interface.
- `start_editalos.bat` simplificado para evitar a abertura de duas janelas do navegador.
- Banco SQLite agora recebe migracao leve para novas colunas de planejamento em `subjects` (`planned_total_minutes`, `planned_weekly_minutes`) durante `init-db`/CLI/Streamlit.

## Fluxo operacional implementado no Streamlit
- Sessao de estudo com controles: iniciar, pausar, retomar, finalizar.
- Sessao vinculada a topico.
- Registro de tempo bruto e liquido da sessao.
- Campo livre na UI para registrar o contexto especifico da sessao (`o que estudei nesta sessao`), persistido ao finalizar.
- Ao finalizar sessao, geracao automatica de revisoes em D+1, D+7, D+15 e D+30.
- Revisoes com status persistido: pendente, concluida, atrasada.
- Acao na interface para marcar revisao como concluida.
- Importacao em lote de topicos por disciplina com suporte a:
- texto com um topico por linha
- secao Markdown com bullets
- CSV com cabecalho `Disciplina,Topico`
- opcao para ignorar topicos ja existentes
- Sincronizacao de pesos por disciplina via colagem de texto gerado por LLM.
- Sincronizacao de metas de tempo por disciplina via colagem de texto gerado por LLM.

## Fluxo de flashcards implementado no Streamlit
- Cadastro manual de flashcard por topico.
- Escolha do algoritmo por card (`FSRS` padrao, `SM-2` opcional).
- Campo opcional de tags por card.
- Fila de revisao com priorizacao de cards vencidos e, depois, cards novos.
- Sessao de revisao com exibicao de frente/verso e botoes `Again`, `Hard`, `Good` e `Easy`.
- Persistencia de historico de revisoes em `card_reviews` e estado agendado em `card_schedule_states`.
- Vinculo opcional do flashcard a uma `study_session` finalizada, compartilhando o mesmo contexto da revisao.
- Tabela operacional com base de flashcards cadastrados, incluindo proximo vencimento, reps e lapses.

## Painel operacional implementado
- "O que estudar hoje" (plano diario via PlannerService).
- Revisoes vencidas.
- Proximas revisoes (janela de 30 dias).
- Tempo estudado no dia (minutos).
- Quantidade de revisoes concluidas no dia.
- Tabela de disciplinas com peso, quantidade de questoes e metas total/semanal de tempo.

## Estrutura de dados operacional
- Novos modelos/tabelas adicionados com compatibilidade SQLite:
- `study_session_runs` (controle operacional de sessao: estado, pausas, bruto/liquido)
- `review_tasks` (agenda de revisoes por topico)
- `topic_progress` (acumulados por topico: sessoes, tempo, revisoes)
- Tabela `study_sessions` existente foi preservada e continua sendo alimentada ao finalizar sessao.

## Compatibilidade e integridade
- Cadastros existentes (disciplina/topico) preservados.
- CLI validada e nao quebrada (`--help` e `init-db` funcionando).
- Regras de negocio da nova operacao centralizadas em `services/`.
- Novo comando CLI `import-topics` disponivel para importar topicos por arquivo, filtrando pela disciplina selecionada.
- Novos comandos CLI `sync-subject-weights` e `sync-subject-time` disponiveis para sincronizacao por arquivo.
- Planner agora considera metas semanais por disciplina como fator de balanceamento quando houver defasagem de estudo.
- Revisoes na UI agora podem exibir contexto resumido da sessao de origem e quantidade de flashcards vinculados.

## Snapshot atual do banco local (2026-03-10)
- `subjects`: 2
- `topics`: 0
- `cards`: 0
- `study_materials`: 0
- `study_sessions`: 0
- `study_session_runs`: 0
- `review_tasks`: 0
- `topic_progress`: 0
- `sleep_logs`: 0
- `hydration_logs`: 0
- `nutrition_logs`: 0
- `exercise_logs`: 0
- `supplement_logs`: 0
- `embedding_vectors`: 0

## Handoff objetivo
- Implementado: fase operacional de execucao de estudo + revisao espacada + painel diario.
- Implementado: importacao em lote de topicos por disciplina na UI e na CLI.
- Implementado: sincronizacao de pesos por disciplina e metas de tempo por disciplina na UI e na CLI.
- Implementado: primeira release de flashcards no Streamlit usando o backend existente de cards + FSRS/SM-2.
- Implementado: revisao com contexto de sessao e vinculo opcional de flashcards a sessao/revisao.
- Falta (proxima frente): logs de biohacking na interface Streamlit.

## Validacao executada nesta atualizacao
- `.venv\Scripts\python.exe -m py_compile editalos\database.py editalos\models.py editalos\schemas.py editalos\services\study_execution.py editalos\services\flashcards.py editalos\ui\streamlit_app.py tests\test_study_execution.py tests\test_flashcards.py` (ok)
- Smoke test SQLite in-memory para finalizar sessao com `content_summary`, gerar revisao futura com contexto e criar flashcard vinculado a mesma `study_session` (ok)
- `.venv\Scripts\python.exe -m py_compile editalos\services\study_execution.py tests\test_study_execution.py` (ok)
- Smoke test com `StudyExecutionService.session_snapshot()` usando `started_at`/`last_resumed_at` naive e `now_utc()` aware (ok)
- `.venv\Scripts\python.exe -m py_compile editalos\services\flashcards.py editalos\ui\streamlit_app.py tests\test_flashcards.py` (ok)
- Smoke test SQLite in-memory para criacao de flashcard, entrada na fila, revisao `good` via FSRS e saida da fila (ok)
- `.venv\Scripts\python.exe -m pytest tests\test_flashcards.py -q` nao executado nesta etapa (modulo `pytest` ausente na `.venv`)
- `.venv\Scripts\python.exe -m py_compile editalos\database.py editalos\models.py editalos\schemas.py editalos\services\catalog.py editalos\services\planner.py editalos\cli.py editalos\ui\streamlit_app.py tests\test_catalog.py` (ok)
- Smoke test SQLite in-memory para sincronizacao de pesos e tempo com atualizacao de `weight`, `planned_total_minutes` e `planned_weekly_minutes` (ok)
- `.venv\Scripts\python.exe -m py_compile editalos\config.py editalos\database.py editalos\ui\streamlit_app.py` (pendente nesta etapa)
- `.venv\Scripts\python.exe -m py_compile editalos\services\catalog.py editalos\ui\streamlit_app.py editalos\cli.py tests\test_catalog.py` (ok)
- `.venv\Scripts\python.exe -m editalos.cli --help` (ok, `import-topics` listado)
- Smoke test em memoria para `CatalogService.import_topics_for_subject` com Markdown e CSV, incluindo ignorar duplicados (ok)
- `.venv\Scripts\python.exe -m pytest tests\test_catalog.py -q` nao executado nesta etapa (modulo `pytest` ausente na `.venv`)
- `.venv\Scripts\python.exe -m py_compile editalos\enums.py editalos\models.py editalos\services\study_execution.py editalos\ui\streamlit_app.py tests\test_study_execution.py` (ok)
- `.venv\Scripts\python.exe -m editalos.cli --help` (ok)
- `.venv\Scripts\python.exe -m editalos.cli init-db` (ok)
- Smoke test SQLite in-memory para sessao operacional + geracao de revisoes (ok)
- `tests/test_study_execution.py` adicionado para cobrir regras de sessao/revisao/progresso
- `.venv\Scripts\python.exe -m pytest -q` nao executado nesta etapa (modulo `pytest` ausente na `.venv`)

## Restricoes atuais
- execucao local Windows
- banco local SQLite
- sem empacotamento `.exe` nesta etapa
