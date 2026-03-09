# AGENTS.md — EditalOS

## Objetivo do projeto
EditalOS é um software local de apoio a estudo para concursos com:
- revisão espaçada
- analytics
- biohacking
- banco SQLite local
- interface Streamlit
- arquitetura Python organizada em pacote

## Stack atual
- Python 3.11+
- SQLite
- SQLAlchemy
- Streamlit
- pacote principal: `editalos`

## Estado atual
- banco `editalos.db` funcionando
- CLI funcionando
- dashboard Streamlit funcionando
- 1 disciplina cadastrada para teste
- interface ainda não possui formulários de entrada

## Regras de implementação
1. Não quebrar os comandos já validados.
2. Preferir mudanças pequenas e revisáveis.
3. Sempre manter compatibilidade com execução local Windows.
4. Não remover funcionalidades existentes sem justificar.
5. Sempre atualizar `PROJECT_STATUS.md` ao final de uma tarefa relevante.
6. Sempre informar quais arquivos foram alterados.
7. Sempre propor validação manual e, se possível, validação automatizada.

## Convenções
- manter imports absolutos do pacote `editalos`
- preferir funções pequenas e legíveis
- evitar lógica grande diretamente em `streamlit_app.py`; criar helpers em `services/` quando necessário
- UI em português
- código e nomes internos em inglês, quando fizer sentido técnico
- tratar erros com mensagens amigáveis na interface

## Prioridade atual
Transformar o Streamlit de dashboard em interface operacional.

## Próximas features prioritárias
1. cadastro de disciplina
2. cadastro de tópico
3. sessão de estudo com start/pause/finish
4. logs de biohacking
5. upload de PDF