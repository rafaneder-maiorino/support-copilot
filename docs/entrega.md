# Entrega — Datathon Fase 5

Fonte do documento de entrega (PDF) enviado na plataforma da FIAP. O PDF é gerado a partir deste
arquivo no dia do envio, depois que o link do vídeo existir; o RM é preenchido só no PDF.

**Projeto:** Support Copilot — triagem, RAG e guardrails para suporte técnico
**Curso:** Pós Tech em Machine Learning Engineering (FIAP) · Datathon da Fase 5 · Grupo 11 (individual)

## Links

| o quê | link |
|---|---|
| Repositório (público) | https://github.com/rafaneder-maiorino/support-copilot |
| README (como rodar, nuvem, LGPD) | https://github.com/rafaneder-maiorino/support-copilot#readme |
| Vídeo STAR (YouTube, não listado) | https://youtu.be/PENDENTE |
| Relatório de drift (Evidently) | https://github.com/rafaneder-maiorino/support-copilot/blob/main/reports/drift_report.html |
| Requisitos × evidência | https://github.com/rafaneder-maiorino/support-copilot/blob/main/docs/requisitos.md |

## Como avaliar em 3 passos

1. `git clone https://github.com/rafaneder-maiorino/support-copilot.git && cd support-copilot`
2. `docker compose up --build` (primeiro build ~6 min; não precisa de chave de API: o `/chat` roda
   com o provedor `fake`)
3. Swagger em `http://localhost:8000/docs` e MLflow em `http://localhost:5001`

## Onde está cada entregável do enunciado

| entregável | arquivo |
|---|---|
| Código-fonte (treino, API, RAG, drift) | [`src/support_copilot/`](../src/support_copilot/) |
| Relatório HTML do Evidently | [`reports/drift_report.html`](../reports/drift_report.html) e o controle [`reports/drift_report_control.html`](../reports/drift_report_control.html) |
| Dockerfile e requirements.txt | [`Dockerfile`](../Dockerfile), [`requirements.txt`](../requirements.txt) |
| README com arquitetura de nuvem e LGPD | [`README.md`](../README.md) |
| Vídeo | link acima |

## Números principais

| área | resultado |
|---|---|
| Triagem (10 filas) | F1 macro 0,371 contra 0,045 da classe majoritária, em teste separado por família de paráfrases |
| RAG em perguntas novas | recall@4 0,938 · hit@1 0,750 |
| Guardrails | 0/263 injeções externas chegam ao LLM · 0/2.110 chamados reais bloqueados por engano |
| Drift | alemão detectado em 20/20 lotes (F1 −0,190); controle limpo em 0/20 |
