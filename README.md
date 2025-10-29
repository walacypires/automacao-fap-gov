# FAP Automação (Consulta FAP – gov.br)

Automação em Python com Selenium para consultar o FAP (Fator Acidentário de Prevenção) no portal gov.br, anexando ao navegador Brave já aberto (DevTools/remote debugging) e exportando os resultados para planilha.

## Visão geral

Este projeto automatiza o fluxo de consulta do FAP para múltiplas combinações de parâmetros:
- Seleciona a vigência (anos 2025 e 2026 por padrão)
- Percorre todos os CNPJs raiz e seus estabelecimentos disponíveis na página
- Realiza a consulta para cada combinação e extrai os dados do resultado
- Gera um relatório em `.xlsx` (com fallback para `.csv` caso o `openpyxl` não esteja instalado)

Os dados extraídos incluem:
- CNPJ_Raiz, Razao_Social, CNPJ_Estab, Estab_Nome
- UF e Município (com parser para tratar endereços completos como “Município - UF CEP …”)
- Vigência, Alíquota e Data_Consulta

## Principais recursos

- Anexação ao Brave via `--remote-debugging-port` (não abre/fecha o navegador à força)
- Uso do perfil ativo do Brave (ou perfil definido) para reaproveitar sessão e certificados
- Validação opcional de “IP pinning” antes de automatizar (TLS + SAN) para garantir que os hosts respondem pelos IPs esperados
- Auxiliares robustos para dropdowns/combobox (clique, digitação, opção por texto, fallback de primeira opção)
- Tratamento opcional do diálogo nativo de certificado do Windows via `pywinauto`
- Exportação de resultados para Excel/CSV

## Estrutura do código

- `main.py` – Fluxo principal (seleção de vigência, iteração CNPJ/Estabelecimento, consulta, extração e gravação)
- `browser_config.py` – Configuração do navegador (Brave + Selenium), anexando via DevTools
- `sso_utils.py` – Utilitários de UI/SSO (cliques, dropdowns, reset de sessão, diálogo de certificado)
- `net_utils.py` – Validação de IP/host com verificação de certificado (SAN) via TLS
- `report_utils.py` – Escrita do relatório (`.xlsx` com `openpyxl`; fallback `.csv`)
- `launcher_ip/` – Scripts auxiliares (ex.: iniciar Brave com regras de IP e porta de debug)

## Requisitos

- Windows (o fluxo usa Brave e, opcionalmente, diálogo nativo de certificado)
- Brave instalado
- Python 3.8+
- Pacotes Python:
  - `selenium`, `webdriver-manager`
  - `openpyxl` (opcional, para `.xlsx`; sem ele, grava `.csv`)
  - `pywinauto` (opcional, para aceitar diálogo nativo de certificado)

## Configuração

- `browser_config.py`:
  - `ATTACH_DEBUGGER` – Endereço do DevTools do Brave (ex.: `127.0.0.1:9222`)
  - `KEEP_OPEN`, `PROFILE_DIR_OVERRIDE` – Comportamento da janela e perfil
  - Suporte a `PROXY_URL` e regras de host pode ser estendido se necessário

- `main.py`:
  - `BIND_DEST_IPS` – Hosts “pinnados” para IPs
  - `VALIDATE_IPS_BEFORE` – Ativa validação TLS/SAN antes de iniciar
  - XPaths dos campos e resultados (ajuste se o HTML mudar)

- Inicie o Brave com DevTools:
  - Via script (ex.: `launcher_ip/brave-pinned.ps1`) ou manualmente com `--remote-debugging-port=9222`

## Como executar (PowerShell)

```powershell
# (Opcional) Ative um ambiente virtual e instale dependências
# py -m venv .venv; .\.venv\Scripts\Activate.ps1
# py -m pip install selenium webdriver-manager openpyxl pywinauto

# Inicie o Brave com remote debugging (exemplo; ajuste conforme seu ambiente)
# & "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" --remote-debugging-port=9222

# Execute a automação
py .\main.py
```

O relatório será salvo como `relatorio_fap.xlsx` (ou `relatorio_fap.csv` se faltar o `openpyxl`).

## Solução de problemas

- Timeout ao abrir dropdowns
  - O projeto usa fallbacks (JS click, ALT+DOWN, SPACE). Se persistir, verifique se a lista depende de seleção anterior (ex.: primeiro selecione a vigência).
- “Consultas” não retornam dados
  - Confirme que o Brave está anexado (`ATTACH_DEBUGGER` correto) e que o certificado cliente está acessível pelo perfil.
- `openpyxl` ausente
  - O arquivo será gravado em `.csv`. Instale `openpyxl` para `.xlsx`.
- Diálogo de certificado travando o fluxo
  - Ative o watcher com `pywinauto` (ver `watch_and_accept_cert_dialog` em `sso_utils.py`).
- Validação de IP/host falhando
  - Desative `VALIDATE_IPS_BEFORE` ou ajuste `BIND_DEST_IPS` conforme sua rede.

## Avisos

- Esta automação acessa serviços protegidos do gov.br; use suas credenciais/certificado conforme as políticas da sua organização.
- Ajuste XPaths e tempos de espera caso o HTML do portal mude.