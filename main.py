import threading
import time
import re
from typing import Dict
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from browser_config import (
    start_brave_with_active_profile,
    ATTACH_DEBUGGER,
    KEEP_OPEN,
    PROXY_URL,
)
from sso_utils import (
    safe_click_any_xpath,
    click_dom_ok_if_present,
    watch_and_accept_cert_dialog,
    has_captcha_error,
    reset_sso_session,
    list_options_for_input,
    select_option_by_text,
    set_combobox_value_by_typing,
    select_first_option,
    XPATHS_CERT_BUTTON,
    XPATH_ENTER_GOV,
)
from net_utils import validate_host_ip_map_or_fail
from report_utils import append_row_to_excel
from datetime import datetime

# Sistema alvo (redireciona para SSO)
SSO_URL = "https://fap.dataprev.gov.br/consultar-fap"

# Emissor do seu certificado de cliente (mTLS)
CERT_ISSUER_CN = "AC SOLUTI Multipla v5"

# Hosts "pinnados" para IPs (mesmos do seu .ps1)
BIND_DEST_IPS: Dict[str, str] = {
    "sso.acesso.gov.br": "161.148.168.40",
    "fap.dataprev.gov.br": "200.152.35.17",
}

# (Opcional) aceitar o diálogo NATIVO do Windows do certificado
ACCEPT_NATIVE_CERT_DIALOG = True

# Validar os IPs "pinnados" antes de automatizar (recomendado)
VALIDATE_IPS_BEFORE = True

# Timeouts
TIMEOUT_CLICK = 20
TIMEOUT_LEAVE_SSO = 60
X_COMBO = "/html/body/div[1]/div[2]/div/div[2]/div/div[1]/form/div/div[1]/div/div[1]/div/div/div/input"
X_CNPJ_RAIZ = "/html/body/div/div[2]/div/div[2]/div/div[1]/form/div/div[1]/div/div[2]/div/div/div/input"
X_ESTABELECIMENTOS = "/html/body/div/div[2]/div/div[2]/div/div[1]/form/div/div[1]/div/div[3]/div/div/div/input"
X_BTN_CONSULTA = "/html/body/div/div[2]/div/div[2]/div/div[1]/form/div/div[2]/div/div[2]/button"

# XPaths dos dados do resultado (painel à direita)
X_INFO_ROOT = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[2]"
XP_RAZAO_SOCIAL = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[2]/div/div/div[2]/div/div[1]/span"
XP_CNPJ_ESTAB = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[2]/div/div/div[2]/div/div[2]/div/div[2]/span"
XP_UF = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[2]/div/div/div[2]/div/div[4]/div/div[2]/span"
XP_MUNICIPIO = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[2]/div/div/div[2]/div/div[4]/div/div[2]/span"  # ajustar se necessário

X_VIG_ALIQ_ROOT = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[1]"
XP_ALIQUOTA = "/html/body/div/div[2]/div/div[2]/div[2]/div[1]/div/div[1]/div/div/div[2]/div/div[1]/span"

def _build_host_resolver_rules(host_ip_map: Dict[str, str]):
    # Mantido apenas se for útil no futuro; atualmente não é usado.
    if not host_ip_map:
        return None
    parts = [f"MAP {host} {ip}" for host, ip in host_ip_map.items()]
    parts.append("EXCLUDE localhost")
    return ",".join(parts)


def _safe_text(driver, xpath: str, timeout: int = 10) -> str:
    try:
        el = WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((By.XPATH, xpath)))
        return (el.text or "").strip()
    except Exception:
        try:
            el = driver.find_element(By.XPATH, xpath)
            return (el.text or "").strip()
        except Exception:
            return ""


def _input_value(driver, xpath: str) -> str:
    try:
        el = driver.find_element(By.XPATH, xpath)
        val = el.get_attribute("value")
        return (val or "").strip()
    except Exception:
        return ""


from typing import Tuple


def _parse_municipio_uf(raw_text: str) -> Tuple[str, str]:
    """Extrai Município e UF de um texto de endereço, ex:
    "AL ... GOIANIA - GO CEP: 74.175-020" -> ("GOIANIA", "GO")
    """
    if not raw_text:
        return "", ""
    s = raw_text.strip()
    # remove trecho do CEP
    s = re.sub(r"\bCEP\s*[:：]?\s*\d[\d\.-]*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    # tenta padrão "municipio - UF" (com -, –, /)
    m = re.search(r"(.+?)[\s\-/–]+([A-Z]{2})\s*$", s)
    if m:
        left, uf = m.group(1).strip(), m.group(2).strip()
        # pega o último segmento após vírgula como município
        municipio = left.split(",")[-1].strip()
        # remove sufixos numéricos residuais
        municipio = re.sub(r"\s+\d+$", "", municipio).strip()
        return municipio.upper(), uf.upper()
    # fallback: procurar UF no fim do texto
    m2 = re.search(r"\b([A-Z]{2})\b\s*$", s)
    uf = m2.group(1).upper() if m2 else ""
    left = s[: m2.start()].strip() if m2 else s
    municipio = left.split(",")[-1].strip().upper()
    municipio = re.sub(r"\s+\d+$", "", municipio).strip()
    return municipio, uf


def extract_result_data(driver, ano: str) -> dict:
    """Extrai dados da área de resultado após clicar em Consultar."""
    # Aguarda painel e aliquota
    try:
        WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.XPATH, X_INFO_ROOT)))
    except Exception:
        pass
    try:
        WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.XPATH, XP_ALIQUOTA)))
    except Exception:
        pass

    razao = _safe_text(driver, XP_RAZAO_SOCIAL)
    cnpj_estab = _safe_text(driver, XP_CNPJ_ESTAB)
    # Deriva CNPJ raiz do CNPJ completo (até a barra)
    cnpj_raiz = cnpj_estab.split("/")[0].strip() if cnpj_estab else ""
    uf = _safe_text(driver, XP_UF)
    municipio = _safe_text(driver, XP_MUNICIPIO)
    # Se vier endereço completo, tenta extrair município e UF
    if municipio and ("-" in municipio or "CEP" in municipio.upper()):
        muni_p, uf_p = _parse_municipio_uf(municipio)
        if muni_p:
            municipio = muni_p
        if uf_p:
            uf = uf_p
    elif uf and ("-" in uf or "CEP" in uf.upper()):
        # Alguns layouts podem colocar tudo na mesma div de UF
        muni_p, uf_p = _parse_municipio_uf(uf)
        if muni_p:
            municipio = muni_p
        if uf_p:
            uf = uf_p
    aliquota = _safe_text(driver, XP_ALIQUOTA)
    # Nome do estabelecimento: usa o valor selecionado no combobox
    estab_nome = _input_value(driver, X_ESTABELECIMENTOS)
    # Data/Hora da consulta (dd/mm/aaaa hh:mm:ss)
    data_consulta = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    return {
        "CNPJ_Raiz": cnpj_raiz,
        "Razao_Social": razao,
        "CNPJ_Estab": cnpj_estab,
        "Estab_Nome": estab_nome,
        "UF": uf,
        "Municipio": municipio,
        "Vigencia": str(ano),
        "Aliquota": aliquota,
        "Data_Consulta": data_consulta,
    }


def consultar_para_todos(driver, anos=("2025", "2026")):
    """
    Para cada ano em 'anos', seleciona a vigência e realiza consultas para
    todos os CNPJs raiz e seus estabelecimentos, clicando em "Consultar".
    """
    for ano in anos:
        # Seleciona a vigência (combobox do ano)
        set_combobox_value_by_typing(driver, X_COMBO, str(ano))
        # Opcional: pequena espera para backend carregar CNPJs da vigência
        time.sleep(0.3)

        # Lista de CNPJs raiz para esta vigência
        cnpj_texts = list_options_for_input(driver, X_CNPJ_RAIZ)
        cnpj_texts = [t for t in dict.fromkeys(cnpj_texts)]  # dedupe preservando ordem

        for cnpj in cnpj_texts:
            # Seleciona o CNPJ raiz
            if not select_option_by_text(driver, X_CNPJ_RAIZ, cnpj):
                continue
            time.sleep(5)  # espera backend carregar estabelecimentos
            # Após selecionar o CNPJ, obter estabelecimentos atuais
            estab_texts = list_options_for_input(driver, X_ESTABELECIMENTOS)
            estab_texts = [t for t in dict.fromkeys(estab_texts)]

            for estab in estab_texts:
                ok = select_option_by_text(driver, X_ESTABELECIMENTOS, estab)
                if not ok:
                    # Tenta digitar o texto e confirmar
                    set_combobox_value_by_typing(driver, X_ESTABELECIMENTOS, estab)
                    # Se ainda assim não selecionou, clica na primeira opção como fallback
                    if not select_option_by_text(driver, X_ESTABELECIMENTOS, estab):
                        select_first_option(driver, X_ESTABELECIMENTOS)

                # Clica no botão "Consultar"
                btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, X_BTN_CONSULTA)))
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", btn)
                try:
                    btn.click()
                    time.sleep(5)
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                # Pequena pausa para a consulta processar; ajuste se necessário
                time.sleep(2)

                # Extrai dados do resultado e grava no Excel
                row = extract_result_data(driver, ano)
                append_row_to_excel(
                    path="relatorio_fap.xlsx",
                    row=row,
                    headers=[
                        "CNPJ_Raiz",
                        "Razao_Social",
                        "CNPJ_Estab",
                        "Estab_Nome",
                        "UF",
                        "Municipio",
                        "Vigencia",
                        "Aliquota",
                        "Data_Consulta",
                    ],
                )

def main():
    # Valida IPs "pinnados" antes de automatizar (evita surpresas)
    if VALIDATE_IPS_BEFORE and BIND_DEST_IPS:
        validate_host_ip_map_or_fail(BIND_DEST_IPS)

    driver = start_brave_with_active_profile(
        host_ip_map=BIND_DEST_IPS,
        proxy_url=PROXY_URL,
        keep_open=KEEP_OPEN,
        attach_debugger=ATTACH_DEBUGGER
    )

    stop = threading.Event()
    watcher = None

    try:
        # # 1) Abre o sistema (vai redirecionar para o SSO)
        # driver.get(SSO_URL)
        driver.get("https://fap.dataprev.gov.br/consultar-fap")

        # # Em páginas que mostram "Entrar" primeiro
        # safe_click_any_xpath(driver, XPATH_ENTER_GOV, timeout=10)

        # driver.switch_to.default_content()
        # driver.execute_script(
        #     "document.activeElement && document.activeElement.blur();"
        #     "document.body && document.body.click();"
        # )

        # # 2) Clica no "Seu certificado digital"
        # if ACCEPT_NATIVE_CERT_DIALOG:
        #     watcher = threading.Thread(target=_watch_and_accept_cert_dialog, args=(stop,), daemon=True)
        #     watcher.start()

        # if not safe_click_any_xpath(driver, XPATHS_CERT_BUTTON, timeout=TIMEOUT_CLICK):
        #     driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        #     if not safe_click_any_xpath(driver, XPATHS_CERT_BUTTON, timeout=8):
        #         raise RuntimeError("Não achei o botão 'Seu certificado digital' no gov.br.")

        # click_dom_ok_if_present(driver, timeout=5)

        # # 3) Se acusar captcha inválido, limpa sessão e tenta uma vez
        # if has_captcha_error(driver):
        #     print("[sso] Captcha inválido. Limpando sessão e tentando novamente…")
        #     reset_sso_session(driver)
        #     driver.get(SSO_URL)
        #     driver.switch_to.default_content()
        #     driver.execute_script(
        #         "document.activeElement && document.activeElement.blur();"
        #         "document.body && document.body.click();"
        #     )
        #     if not safe_click_any_xpath(driver, XPATHS_CERT_BUTTON, timeout=TIMEOUT_CLICK):
        #         driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        #         if not safe_click_any_xpath(driver, XPATHS_CERT_BUTTON, timeout=8):
        #             raise RuntimeError("Falha ao acionar 'Seu certificado digital' após reiniciar sessão.")

        # 4) Aguarda sair do domínio do SSO (retorno autenticado)

        # Consulta para todos CNPJs/Estabelecimentos nas vigências desejadas
        consultar_para_todos(driver, anos=["2025", "2026"])

    finally:
        try:
            stop.set()
            if watcher and watcher.is_alive():
                watcher.join(timeout=0.5)
        except Exception:
            pass
        
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
