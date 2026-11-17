# Privacy by Design e LGPD

Como o Support Copilot protege dado pessoal, em especial **antes** de qualquer texto sair para um
LLM de terceiros. Cada camada é marcada como:

- **[IMPLEMENTADO]**: está no código e tem teste ou relatório que prova;
- **[PRESCRITO]**: recomendação para produção, que este repositório não implementa.

> Isto é uma análise de engenharia para um projeto acadêmico, não um parecer jurídico.

## Onde o dado pessoal pode aparecer

Um chamado de suporte é texto livre. O cliente pode escrever e-mail, telefone, CPF, número de cartão,
nome e endereço. O dataset do projeto é público (CC BY-NC 4.0) e não tem dados de clientes reais,
mas o desenho parte do pressuposto de que em produção terá.

| etapa | o que recebe | sai da infraestrutura? |
|---|---|---|
| validação (Pydantic) | texto do chamado | não |
| triagem (classificador) | texto **sem** PII (`strip_pii`) | não |
| guardrail de entrada | texto normalizado | não |
| embeddings + busca FAISS | pergunta **mascarada** | **não**: o modelo de embeddings roda local (fastembed/ONNX) |
| LLM (Gemini/OpenAI) | pergunta **mascarada** + até 4 trechos do manual (documentação pública) | **sim**, e só isto |
| logs da API | hash, tamanho, fila, confiança, rota, vereditos | não (e sem texto) |
| monitoramento de drift | descritores numéricos + saídas do modelo | não (e sem texto) |

## Camadas

### 1. Mascaramento antes do LLM e da busca [IMPLEMENTADO]

`support_copilot.guardrails.pii.mask_pii` troca e-mail, CPF, CNPJ, telefone e cartão por
`[EMAIL]`, `[CPF]`, `[CNPJ]`, `[PHONE]`, `[CARD]` antes da busca e do LLM.

- CPF de 11 dígitos sem pontuação só é mascarado com **dígito verificador válido**; cartão, com
  **Luhn**. Telefone sem separador só conta como celular brasileiro (DDD + 9). Assim número de chamado,
  IP, porta, MTU e MAC não viram `[CPF]` (teste `test_technical_values_are_not_masked`).
- Prova de que o LLM nunca vê o original: um gerador espião registra o que recebe
  (`tests/test_guardrails.py::test_llm_only_sees_the_masked_question` e
  `tests/test_ticket.py::test_pii_in_a_ticket_is_masked_before_retrieval_and_llm`).
- A resposta informa o que foi mascarado em `pii_masked`, para o cliente saber.
- A **saída** do LLM também passa pelo mascaramento (`test_output_pii_is_masked`).

**Limite conhecido:** regex não reconhece nome nem endereço. Conferido:
`"My name is Maria Silva, I live at Rua Augusta 1500, Sao Paulo"` passa sem nenhuma troca. Ver
camada 9.

### 2. A triagem não vê dado pessoal [IMPLEMENTADO]

O classificador recebe o texto com o PII **removido** (`strip_pii`), sem placeholder. Motivo medido:
uma linha "Contact: maria.silva@example.com" mudava a fila de chamados técnicos para Customer
Service; sem PII, 107 rotas trocadas contra 186, a um custo de 0,0016 de F1
([`reports/triage_pii.md`](../reports/triage_pii.md)). Privacidade e qualidade apontaram para o
mesmo lado.

### 3. Minimização: o que não precisa sair, não sai [IMPLEMENTADO]

O princípio da necessidade (Art. 6º, III) aplicado ao fluxo:

- chamados **não técnicos** são roteados para a fila e nunca chegam ao RAG nem ao LLM
  (35,4% dos 3.266 chamados de teste);
- sem trecho relevante no manual (score abaixo de 0,67), o LLM **não é chamado**: a resposta é uma
  recusa local;
- guardrail de entrada bloqueia antes da busca e do LLM;
- no `/ticket` sobre os 3.266 chamados de teste, só **223 (6,8%)** foram respondidos pelo LLM
  (provedor `fake`, [`reports/rag_eval.md`](../reports/rag_eval.md)); os escalados por falta de
  contexto pararam antes dele;
- para o LLM vão só a pergunta mascarada e até `RAG_TOP_K=4` trechos de documentação pública;
- os embeddings são **locais**: a busca não envia texto a ninguém.

### 4. Logs sem texto bruto [IMPLEMENTADO] · pseudonimização mais forte [PRESCRITO]

A API registra um evento JSON por requisição com `text_sha` (SHA-256 do texto, 16 caracteres
hexadecimais), tamanho, fila, confiança, rota, latência e vereditos. Nunca o texto
(`tests/test_api.py::test_logs_never_contain_raw_text`,
`tests/test_api_chat.py::test_chat_logs_never_contain_the_question`,
`tests/test_api_ticket.py::test_ticket_logs_carry_hash_and_action_never_the_text`).

**Ressalva honesta:** um hash sem segredo é **pseudonimização**, não anonimização. Quem tem um texto
candidato consegue calcular o hash e confirmar se ele passou pela API (ataque de dicionário),
principalmente em textos curtos. Pela LGPD, dado pseudonimizado continua sendo dado pessoal se a
reidentificação for possível com meios razoáveis.

**[PRESCRITO]** Trocar por HMAC-SHA-256 com chave guardada no Key Vault: o hash continua estável
(dá para deduplicar e localizar os eventos de um chamado quando o titular pede), mas só quem tem a
chave consegue testar um texto.

### 5. Respostas de erro não devolvem o texto [IMPLEMENTADO]

O 422 padrão do FastAPI/Pydantic devolve o `input` inteiro. O handler do projeto devolve só o tipo
do erro (`test_422_does_not_echo_the_ticket_text`, com e-mail e CPF falsos). Falha do provedor de
LLM vira `502 llm_unavailable`, sem repassar a mensagem do provedor
(`test_provider_failure_returns_502_without_leaking_details`).

### 6. Monitoramento sem texto [IMPLEMENTADO]

O Evidently recebe só descritores numéricos (tamanho, palavras, % fora do vocabulário, % não-letras,
% não-ASCII), calculados depois do `strip_pii`, e as saídas do modelo. Um teste garante que nenhuma
coluna de texto chega ao relatório (`tests/test_drift.py::test_descriptors_never_keep_the_raw_text`). O HTML publicado pode ser compartilhado
sem expor chamados.

### 7. Segredos [IMPLEMENTADO]

A chave do LLM vem só de variável de ambiente (`.env`, nunca versionado). O `.dockerignore` exclui o
`.env` e um teste falha se uma chave puder entrar na imagem
(`tests/test_packaging.py::test_no_secret_can_reach_the_image`). O padrão é `LLM_PROVIDER=fake`,
que não envia nada a ninguém.

### 8. Guardrails como proteção do titular [IMPLEMENTADO]

Além da injeção de prompt, a camada de saída impede que o LLM devolva o prompt do sistema (canários)
e exige citação de página recuperada. Números em
[`reports/guardrails_eval.md`](../reports/guardrails_eval.md): 0/50 falsos positivos nas perguntas
legítimas e 0 bloqueios em 2.110 chamados técnicos reais.

### 9. Nomes e endereços [PRESCRITO]

Acrescentar reconhecimento de entidades (por exemplo Microsoft Presidio com modelo em português)
para `PERSON` e `LOCATION`, medido como o restante do projeto: taxa de detecção num conjunto anotado
e efeito no F1 da triagem e no hit@1 do RAG. Até lá, o risco residual é declarado: nome e endereço
escritos no chamado chegam ao LLM.

## Transferência internacional (Art. 33) [PRESCRITO]

Chamar Gemini ou OpenAI pelas APIs públicas processa a pergunta fora do Brasil: é transferência
internacional de dado pessoal sempre que sobrar dado pessoal depois do mascaramento (por exemplo, um
nome). O mascaramento reduz o risco, mas não elimina a transferência.

| opção | o que muda | observação |
|---|---|---|
| Azure OpenAI com deployment **Standard por geografia** num recurso em Brazil South | processamento na geografia do recurso | o *Global Standard* pode processar em qualquer região; o *Data Zone* hoje cobre EUA e União Europeia. Confirmar a disponibilidade do modelo escolhido nessa modalidade em Brazil South |
| OpenAI API | dados da API não treinam modelos por padrão; logs de abuso guardados por até 30 dias | *Zero Data Retention* só com aprovação. Exige mecanismo do Art. 33 |
| Gemini API **pago** (projeto com faturamento) | prompts não usados para melhorar produtos; logs por tempo limitado, para abuso | exige mecanismo do Art. 33 |
| Gemini API **gratuito** | os termos dizem para **não enviar** informação pessoal: o conteúdo pode ser usado para melhorar produtos e lido por revisores humanos | **não usar em produção**. Na demo do projeto, só com dados fictícios |

Mecanismo para as opções fora do Brasil: as **cláusulas-padrão contratuais** aprovadas pela ANPD
(Resolução CD/ANPD nº 19/2024) no contrato com o provedor, além do acordo de tratamento de dados
(DPA) do próprio provedor, que atua como **operador**.

## Base legal, retenção e governança [PRESCRITO]

| tema | proposta |
|---|---|
| papéis | a empresa que presta o suporte é a **controladora**; o provedor de nuvem e o de LLM são **operadores** |
| base legal (Art. 7º) | **execução de contrato** (V) para responder o chamado; **legítimo interesse** (IX) para monitoramento de drift e melhoria do modelo, com teste de balanceamento registrado. Consentimento não é a base adequada para atender um chamado que o próprio cliente abriu |
| dado sensível (Art. 11) | chamados podem conter dado de saúde ou outro sensível sem o cliente perceber: não reter o texto, e o retreino usa só chamados revisados |
| retenção (Art. 15 e 16) | texto do chamado: no sistema de chamados, pelo prazo do contrato; logs da API (já sem texto): 90 dias no Log Analytics; descritores de drift agregados: 13 meses, para comparar sazonalidade; relatórios HTML: sem dado pessoal, sem prazo |
| direitos do titular (Art. 18) | acesso, correção e eliminação são atendidos no sistema de chamados, onde o texto vive. A API não guarda texto; com o HMAC (camada 4), os eventos de um chamado podem ser localizados e apagados |
| decisão automatizada (Art. 20) | a triagem decide a fila. O `/ticket` nunca descarta um chamado: na dúvida, escala para atendimento humano, e o cliente pode pedir revisão |
| transparência | informar ao cliente que a resposta foi gerada por IA a partir do manual, com as páginas citadas |
| segurança (Art. 46) | identidade gerenciada, private endpoints para Key Vault, Blob e LLM, TLS na borda, criptografia em repouso padrão da plataforma |
| registro e RIPD (Art. 37 e 38) | registrar as operações de tratamento e elaborar o relatório de impacto antes de ligar o LLM com dados reais |
| incidentes (Art. 48) | runbook de comunicação à ANPD e aos titulares; os logs sem texto reduzem o impacto de um vazamento de log |
| encarregado (Art. 41) | indicado pela controladora e divulgado no portal de suporte |

## Fontes

- LGPD, Lei nº 13.709/2018: <https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm>
- ANPD, Resolução CD/ANPD nº 19/2024 (transferência internacional e cláusulas-padrão):
  <https://www.mattosfilho.com.br/?p=107482> (resumo) e
  <https://lefosse.com/noticias/alerta/anpd-publica-regulamento-de-transferencia-internacional-de-dados-e-clausulas-padrao-contratuais-de-protecao-de-dados/>
- Termos adicionais da Gemini API (serviços gratuitos × pagos): <https://ai.google.dev/terms>
- OpenAI, uso de dados da API: <https://developers.openai.com/api/docs/guides/your-data>
- Azure OpenAI, local de processamento por tipo de deployment:
  <https://learn.microsoft.com/azure/ai-foundry/openai/how-to/deployment-types>
