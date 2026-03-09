# EditalOS

EditalOS é um assistente local de estudo orientado por dados para concursos, com:

- planejamento diário por prioridade matemática;
- repetição espaçada com **FSRS** e fallback **SM-2**;
- banco local **SQLite** com camada **SQLAlchemy**;
- logs de estudo, questões, desempenho e biohacking;
- embeddings locais com suporte à API da OpenAI;
- ingestão de PDF do edital;
- geração opcional de jobs para **Batch API** e requisições em **Flex processing**;
- prompts estruturados para aproveitar **Prompt Caching**.

## Aviso importante

Este projeto foi entregue como uma **base robusta e extensível**, não como produto final fechado. O núcleo está funcional e organizado para evolução. Os pontos que naturalmente exigirão iteração com seus dados reais são:

- calibração de pesos do planejador;
- otimização personalizada dos parâmetros do FSRS;
- refinamento do parser de edital por banca/documento;
- tuning dos dashboards e das métricas de biohacking.

## Arquitetura

```text
EditalOS/
├── editalos/
│   ├── config.py
│   ├── database.py
│   ├── enums.py
│   ├── models.py
│   ├── schemas.py
│   ├── cli.py
│   ├── services/
│   │   ├── analytics.py
│   │   ├── embeddings.py
│   │   ├── openai_service.py
│   │   ├── planner.py
│   │   ├── srs.py
│   │   └── pdf_ingest.py
│   └── ui/
│       └── streamlit_app.py
├── tests/
├── requirements.txt
└── .env.example
```

## Instalação

Crie um ambiente virtual e instale as dependências:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

No Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Copie `.env.example` para um arquivo chamado exatamente `.env` na raiz do projeto.

```bash
copy .env.example .env
```

Depois preencha sua chave:

```env
OPENAI_API_KEY=sua_chave_aqui
```

## Banco de dados

Inicialize o banco:

```bash
python -m editalos.cli init-db
```

Isso criará, por padrão, o arquivo `editalos.db` na raiz do projeto.

## Exemplos de uso

### Importar o PDF do edital

```bash
python -m editalos.cli import-pdf --path "C:\\caminho\\para\\edital.pdf" --title "Edital Sefaz"
```

### Criar disciplina

```bash
python -m editalos.cli add-subject --name "Tecnologia da Informação" --weight 2.0 --question-count 12
```

### Criar tópico

```bash
python -m editalos.cli add-topic --subject-id 1 --name "Banco de Dados" --weight 1.3
```

### Criar flashcard

```bash
python -m editalos.cli add-card --topic-id 1 --front "O que é normalização?" --back "Processo de organização dos dados..." --algorithm fsrs
```

### Revisar flashcard

FSRS usa rating `again|hard|good|easy`.

```bash
python -m editalos.cli review-card --card-id 1 --rating good
```

### Registrar bloco de estudo

```bash
python -m editalos.cli log-study \
  --subject-id 1 \
  --topic-id 1 \
  --started-at "2026-03-08T19:00:00" \
  --ended-at "2026-03-08T20:20:00" \
  --energy 4 --focus 5 --sleepiness 2 \
  --retention 4 --difficulty 3 --fatigue 2 --accuracy 0.78
```

### Registrar sono

```bash
python -m editalos.cli log-sleep --sleep-start "2026-03-07T23:10:00" --wake-up "2026-03-08T05:20:00" --quality 3
```

### Buscar materiais semanticamente

```bash
python -m editalos.cli semantic-search --query "normalização em banco de dados" --top-k 5
```

### Gerar plano diário

```bash
python -m editalos.cli plan-day --minutes 240
```

## Dashboard local

```bash
streamlit run editalos/ui/streamlit_app.py
```

## OpenAI: custo e performance

O projeto já vem preparado para:

- **Responses API**.
- **Structured Outputs** com JSON Schema estrito.
- **Prompt Caching**, que funciona automaticamente, mas foi favorecido pela forma como os prompts foram estruturados no código.
- **Flex processing**, para tarefas longas e não urgentes por menor custo.
- **Batch API**, para jobs assíncronos em lote, via arquivo JSONL.
- **Embeddings**.

## Estratégia de repetição espaçada

O projeto usa as bibliotecas oficiais/open implementations de referência:

- `fsrs`, com `Scheduler`, `Card`, `Rating` e cálculo de retrievability, e possibilidade de customizar desired retention e parâmetros.
- `sm-2`, como baseline de fallback e comparação.

## Observações

- O `.env` deve ficar na raiz do software e ter exatamente esse nome.
- O projeto foi pensado para rodar **100% local**, com SQLite.
- Embeddings são armazenados no banco como JSON para simplicidade do MVP local.
- O parser de edital aceita melhoria incremental conforme você alimentar o sistema com o PDF real e as estruturas de banca que deseja extrair.
