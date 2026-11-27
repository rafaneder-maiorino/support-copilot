# Requisitos do enunciado × evidência

Revisão final contra o enunciado (`MLET - Datathon - Fase 5.pdf`): cada requisito, o arquivo que o
cumpre e o teste que prova. A tabela é conferida por `tests/test_requirements.py`: todo arquivo,
módulo, alvo do `Makefile` e teste citado precisa existir, e todo requisito que não está `ok` precisa
dizer o que falta.

**Situação em 23/11/2026:** 25 ok · 1 parcial · 1 pendente.

| ID | requisito | evidência | prova automática | status |
|---|---|---|---|---|
| R01 | Modelo clássico (Scikit-learn/XGBoost) que classifica o assunto do ticket | `src/support_copilot/model/train.py` (TF-IDF + LogReg campeã contra LinearSVC e XGBoost), `models/ticket_classifier.joblib` | `test_run_registers_champion_and_exports_artifacts` | ok |
| R02 | Dataset público de tickets de suporte | `src/support_copilot/data/source.py` (SHA-256 fixado), `docs/dataset.md` (origem e licença CC BY-NC 4.0), `reports/dataset_audit.md`; `make download` | `test_download_with_a_different_hash_fails_loudly`, `test_download_is_accepted_when_the_hash_matches` | ok |
| R03 | Limpeza dos dados | `src/support_copilot/data/prepare.py`, `reports/prepare_summary.md`; `make prepare` | `test_clean_filters_language_empty_duplicates_and_length`, `test_split_keeps_families_on_one_side_and_is_stratified` | ok |
| R04 | Métricas Acurácia e F1 registradas | runs do experimento `ticket-triage` no MLflow (`cv_accuracy_mean`, `cv_f1_macro_mean`, `test_accuracy`, `test_f1_macro`, matriz de confusão), `reports/model_selection.md`, `docs/img/mlflow-runs-grouped.jpg` | `test_run_registers_champion_and_exports_artifacts` | ok |
| R05 | Servidor local do MLflow | `make mlflow-server` (porta 5001), serviço `mlflow` no `docker-compose.yml` | `test_compose_runs_api_and_mlflow_on_the_agreed_ports` | ok |
| R06 | Inferência do modelo em FastAPI | `POST /predict` em `src/support_copilot/api/main.py` | `test_predict_valid_ticket` | ok |
| R07 | Segundo endpoint `/chat` | `POST /chat` em `src/support_copilot/api/main.py` | `test_in_domain_question_is_answered_with_page_citations` | ok |
| R08 | LangChain ou LlamaIndex | LangChain: `PyPDFLoader`, `RecursiveCharacterTextSplitter` e `FAISS` em `src/support_copilot/rag/ingest.py`; dependências no `pyproject.toml` | `test_split_assigns_sequential_chunk_ids_and_keeps_metadata` | ok |
| R09 | Carregar manual em PDF | `data/manuals/opnsense-support-manual.pdf` (BSD 2-Clause, `data/manuals/NOTICE-opnsense-docs.txt`) | `test_manual_is_pinned_and_sections_come_from_outline` | ok |
| R10 | Chunking | 1500 caracteres com 15% de sobreposição, escolhido entre 3 tamanhos em `reports/retrieval_eval.md` | `test_split_assigns_sequential_chunk_ids_and_keeps_metadata` | ok |
| R11 | Banco vetorial em memória (FAISS ou Chroma local) | FAISS em memória, embeddings locais (fastembed), `src/support_copilot/rag/retriever.py` | `test_known_questions_retrieve_their_section`, `test_warmup_builds_the_index_once_then_reuses_it` | ok |
| R12 | LLM OpenAI ou Gemini via API | `src/support_copilot/rag/llm.py` com `LLM_PROVIDER=gemini` ou `openai` (chave só por variável de ambiente, `.env.example`) | `test_adapter_sends_system_prompt_then_context_and_question`, `test_unknown_provider_and_missing_key_fail_fast` | parcial — falta: uma chamada real ao Gemini ou à OpenAI, que depende da chave do Rafael no `.env`; o adaptador foi testado com um modelo espião |
| R13 | Responder baseado apenas no PDF | `src/support_copilot/rag/prompts.py` (contexto como dado, recusa sem base), limiar de similaridade e guardrail de saída (citação de página recuperada) | `test_out_of_domain_question_is_refused_without_sources`, `test_uncited_answer_is_replaced_by_the_refusal`, `test_real_provider_path_still_goes_through_the_output_guardrail` | ok |
| R14 | Guardrails: palavras proibidas | `src/support_copilot/guardrails/rules.yaml`, `src/support_copilot/guardrails/input.py` | `test_forbidden_words_are_blocked`, `test_forbidden_words_match_whole_words_only` | ok |
| R15 | Guardrails: tentativa de fazer a IA ignorar o PDF é bloqueada | detector de injeção direta (entrada) e indireta (contexto), `reports/guardrails_eval.md` | `test_injection_attempts_are_blocked`, `test_attacks_get_400_naming_the_guardrail`, `test_blocked_input_never_reaches_retrieval_or_llm` | ok |
| R16 | Script que simula degradação (data drift) | `src/support_copilot/drift/simulate.py` (5 cenários semeados); `make drift-simulate` | `test_scenarios_are_reproducible_with_the_same_seed`, `test_text_noise_degrades_but_keeps_the_api_contract` | ok |
| R17 | Relatório HTML do Evidently AI | `reports/drift_report.html`, gerado por `src/support_copilot/drift/report.py`; `make drift-report` | `test_calibrated_thresholds_are_used_by_evidently` | ok |
| R18 | O Evidently aponta o erro visualmente | "Dataset Drift is detected" em `reports/drift_report.html`; "Dataset Drift is NOT detected" no controle `reports/drift_report_control.html` | `test_evidently_flags_a_shifted_batch_and_not_a_clean_one` | ok |
| R19 | Validação com Pydantic ou Pandera (texto não vazio, ≤ 1000 caracteres) | `src/support_copilot/api/schemas.py` (Pydantic), `src/support_copilot/data/schemas.py` (Pandera para lotes) | `test_invalid_payloads_return_422`, `test_predict_accepts_exactly_max_chars`, `test_contract_violations_are_reported` | ok |
| R20 | Dockerfile que sobe a API e instala as dependências | `Dockerfile` (multi-stage, não-root, `HEALTHCHECK`); job `docker` do CI sobe o compose e faz smoke test (`.github/workflows/ci.yml`) | `test_dockerfile_installs_from_requirements_and_drops_root`, `test_no_secret_can_reach_the_image` | ok |
| R21 | requirements.txt | `requirements.txt` exportado do `uv.lock`; `make requirements`; o CI compara com um `uv export` novo | `test_every_pin_matches_uv_lock`, `test_requirements_header_records_the_export_command` | ok |
| R22 | README: arquitetura de nuvem (AWS/Azure/GCP) | seção "Arquitetura de nuvem" do `README.md` (Mermaid) e `docs/arquitetura-nuvem.md` | `test_readme_has_the_sections_the_challenge_asks_for` | ok |
| R23 | README: Privacy by Design / LGPD (proteção antes do LLM) | seção "Privacy by Design e LGPD" do `README.md`, `docs/lgpd.md`, `src/support_copilot/guardrails/pii.py` | `test_llm_only_sees_the_masked_question`, `test_pii_in_a_ticket_is_masked_before_retrieval_and_llm`, `test_cited_test_names_exist` | ok |
| R24 | Código-fonte completo: treino, API, pipeline RAG, script de drift | `support_copilot.model.train`, `support_copilot.api.main`, `support_copilot.rag.chat`, `support_copilot.drift.report` | `test_relative_links_resolve` | ok |
| R25 | Vídeo de 5 min, formato STAR (API, MLflow, drift) | roteiro `docs/video-notes.md` e cenas `docs/demo/scenes.json` | `test_script_follows_star_and_ends_by_five_minutes`, `test_scene_behaves_as_the_script_says` | pendente — falta: gravar, publicar no YouTube como não listado e testar o link sem login (Rafael, D13) |
| R26 | Contexto: chamado técnico aciona o RAG (triagem → roteamento) | `POST /ticket`, `src/support_copilot/ticket.py` | `test_technical_ticket_in_the_manual_is_answered_with_citations`, `test_billing_ticket_is_routed_to_its_queue_without_an_answer` | ok |
| R27 | Contexto: a observabilidade detecta drift nos dados do classificador | descritores do texto + saídas do modelo em `reports/drift_summary.md` (alemão 20/20 lotes, controle 0/20) | `test_descriptors_separate_english_from_german`, `test_evidently_flags_a_shifted_batch_and_not_a_clean_one` | ok |

## Fora da matriz, mas na rubrica

| item | situação |
|---|---|
| Docker funcionando num clone limpo | macOS validado em 19/11 (`docs/validacao.md`); **Windows 11 pendente** (Rafael, roteiro no fim de `docs/validacao.md`) |
| Links externos acessíveis sem login | 14/15 em 23/11 (`uv run python -m support_copilot.links`); o que falha é o link deste arquivo no `main`, que só existe depois do merge. Repetir depois do merge e de incluir o link do vídeo |
| Entregáveis do enunciado (código, HTML do Evidently, Dockerfile + requirements.txt, README, vídeo) | 4 de 5 no repositório; o vídeo é o R25 |

## Divergências do plano, registradas

- O plano previa `reports/inspection.md` para a limpeza (R03); a evidência ficou dividida em
  `reports/dataset_audit.md` (auditoria do dataset) e `reports/prepare_summary.md` (resultado da limpeza).
- O plano citava `rag/chain.py`; a cadeia ficou em `src/support_copilot/rag/chat.py`.
