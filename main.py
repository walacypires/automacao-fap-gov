import threading
import time
import re
from typing import Dict
import os, logging
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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, StaleElementReferenceException

# -----------------------------------------------------------------------------
# Logging e esperas
# -----------------------------------------------------------------------------
def _setup_logging():
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join("logs", f"run-{ts}.log")
    logger = logging.getLogger("fapbot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.handlers.clear()
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.info(f"Log iniciado: {log_path}")
    return logger

LOG = _setup_logging()

# Esperas solicitadas
SLEEP_AFTER_TYPE = 3       # após inserir/selecionar em combobox
SLEEP_AFTER_CONSULT = 5    # após clicar em Consultar

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
VALIDATE_IPS_BEFORE = False  # validação desnecessária pois a pinagem é feita no processo do Brave

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

# CSS do input CNPJ Raiz (fornecido)
CNPJ_INPUT_CSS = "#cnpjRaiz"

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
    LOG.info("Extraindo resultado...")
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
    LOG.info(f"OK: {cnpj_raiz} | {estab_nome} | UF={uf} | Aliquota={aliquota}")

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


def _find_el(driver, css: str = None, xpath: str = None):
    if css:
        return WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
    return WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, xpath)))


def _open_dropdown_via_button(driver, css: str = None, xpath: str = None):
    """Abre o dropdown clicando no botão 'Exibir lista' irmão do input."""
    LOG.debug(f"Abrindo dropdown via botão: css={css} xpath={xpath}")
    el = _find_el(driver, css, xpath)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    container = None
    try:
        container = el.find_element(By.XPATH, "./ancestor::div[contains(@class,'br-input')][1]")
    except Exception:
        container = el.find_element(By.XPATH, "./parent::*")
    # botão com ícone de seta
    btn = None
    try:
        btn = container.find_element(By.CSS_SELECTOR, "button.br-button")
    except Exception:
        btn = container.find_element(By.XPATH, ".//button")
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)
    # espera a lista abrir (role=listbox) ou opções visíveis
    try:
        WebDriverWait(driver, 5).until(
            lambda d: len(_visible_option_elements(d)) > 0
        )
    except TimeoutException:
        # como fallback, tenta ALT+DOWN no input
        try:
            el.click()
            el.send_keys(Keys.ALT, Keys.ARROW_DOWN)
        except Exception:
            pass
    return el


def _visible_option_elements(driver):
    """Retorna elementos de opção visíveis do dropdown atual (genérico)."""
    candidates = []
    sels = [
        (By.CSS_SELECTOR, "[role='listbox'] [role='option']"),
        (By.CSS_SELECTOR, "ul[role='listbox'] li"),
        (By.XPATH, "//div[@role='option']"),
        (By.XPATH, "//ul[@role='listbox']//li"),
        (By.CSS_SELECTOR, ".br-list .item"),
    ]
    seen = set()
    for by, sel in sels:
        try:
            for e in driver.find_elements(by, sel):
                if e.is_displayed():
                    tid = id(e)
                    if tid not in seen:
                        seen.add(tid)
                        candidates.append(e)
        except Exception:
            pass
    # remove placeholders “Selecione …”
    def _txt(e):
        try:
            return e.text.strip()
        except Exception:
            return ""
    return [e for e in candidates if _txt(e) and "SELECIONE" not in _txt(e).upper()]


def _list_options_for_input(driver, css: str = None, xpath: str = None):
    """Abre a lista pelo botão ao lado do input e retorna todos os textos."""
    _open_dropdown_via_button(driver, css, xpath)
    opts = _visible_option_elements(driver)
    texts = []
    for e in opts:
        try:
            t = e.text.strip()
            if t:
                texts.append(t)
        except Exception:
            pass
    # dedupe preservando ordem
    return [t for t in dict.fromkeys(texts)]


def _find_listbox_container(driver):
    """Retorna o container rolável (role=listbox) do dropdown aberto."""
    sels = [
        (By.CSS_SELECTOR, "[role='listbox']"),
        (By.CSS_SELECTOR, "ul[role='listbox']"),
        (By.CSS_SELECTOR, ".br-list"),
    ]
    for by, sel in sels:
        try:
            for el in driver.find_elements(by, sel):
                if el.is_displayed():
                    return el
        except Exception:
            pass
    # fallback: usa o ancestral do primeiro option visível
    opts = _visible_option_elements(driver)
    if opts:
        try:
            return opts[0].find_element(By.XPATH, "./ancestor::*[self::ul or @role='listbox' or contains(@class,'br-list')][1]")
        except Exception:
            return opts[0].find_element(By.XPATH, "./parent::*")
    return None


def _list_all_options_scrolling(driver, css: str = None, xpath: str = None, pause: float = 0.15, max_scrolls: int = 300):
    """Abre o dropdown e varre com scroll para capturar TODAS as opções (virtualização)."""
    LOG.info("Coletando opções com scroll (fallback)...")
    _open_dropdown_via_button(driver, css, xpath)
    container = _find_listbox_container(driver)
    if not container:
        return _list_options_for_input(driver, css, xpath)  # fallback

    # vai para o topo
    try:
        driver.execute_script("arguments[0].scrollTop = 0;", container)
    except Exception:
        pass

    seen_list = []
    seen_set = set()
    last_count = -1
    still_counter = 0

    for i in range(max_scrolls):
        # coleta visíveis
        for e in _visible_option_elements(driver):
            try:
                t = e.text.strip()
                if t and "SELECIONE" not in t.upper() and t not in seen_set:
                    seen_set.add(t)
                    seen_list.append(t)
            except Exception:
                pass

        # fim se nada novo por algumas iterações E chegou no fim do scroll
        new_count = len(seen_list)
        reached_end = False
        try:
            top = driver.execute_script("return arguments[0].scrollTop;", container)
            ch = driver.execute_script("return arguments[0].clientHeight;", container)
            sh = driver.execute_script("return arguments[0].scrollHeight;", container)
            reached_end = (top + ch + 2) >= sh
        except Exception:
            pass

        if new_count == last_count:
            still_counter += 1
        else:
            still_counter = 0
            last_count = new_count

        if reached_end and still_counter >= 1:
            break

        # scroll passo
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight;", container)
        except Exception:
            # fallback com END
            try:
                _find_el(driver, css, xpath).send_keys(Keys.END)
            except Exception:
                pass
        time.sleep(pause)

    return seen_list


def _select_option_by_text_via_button(driver, text: str, css: str = None, xpath: str = None, max_scrolls: int = 300) -> bool:
    """Abre a lista e rola até encontrar o texto; se não achar, digita e ENTER."""
    input_el = _open_dropdown_via_button(driver, css, xpath)
    lo = (text or "").strip().lower()

    def _try_click_visible():
        for e in _visible_option_elements(driver):
            try:
                t = e.text.strip()
                if t and (t.lower() == lo or t.lower().startswith(lo)):
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
                    try:
                        e.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", e)
                    return True
            except Exception:
                pass
        return False

    # tenta no que está visível
    if _try_click_visible():
        return True

    # rola até achar
    container = _find_listbox_container(driver)
    if container:
        for _ in range(max_scrolls):
            try:
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollTop + Math.max(40, arguments[0].clientHeight*0.9);", container)
            except Exception:
                try:
                    input_el.send_keys(Keys.PAGE_DOWN)
                except Exception:
                    pass
            time.sleep(0.12)
            if _try_click_visible():
                return True

    # fallback: digitar valor e ENTER
    try:
        input_el.click()
        input_el.send_keys(Keys.CONTROL, "a")
        input_el.send_keys(Keys.BACKSPACE)
        input_el.send_keys(text)
        time.sleep(0.2)
        input_el.send_keys(Keys.ENTER)
        return True
    except Exception:
        return False


def _select_first_option_via_button(driver, css: str = None, xpath: str = None) -> bool:
    _open_dropdown_via_button(driver, css, xpath)
    opts = _visible_option_elements(driver)
    if not opts:
        return False
    target = opts[0]
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
        target.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", target)
            return True
        except Exception:
            return False


def _type_select(driver, text: str, css: str = None, xpath: str = None) -> bool:
    """Seleciona no combobox digitando o valor completo e pressionando ENTER."""
    try:
        LOG.info(f"Selecionando por digitação: {text}")
        el = _find_el(driver, css, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        time.sleep(0.05)
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        if text:
            el.send_keys(text)
            time.sleep(0.15)
            el.send_keys(Keys.ENTER)
        time.sleep(SLEEP_AFTER_TYPE)  # espera solicitada
        return True
    except Exception:
        return False


def _get_active_option(driver, input_el):
    """Retorna o elemento da opção atualmente ativa (highlight) para um combobox acessível."""
    try:
        act_id = input_el.get_attribute("aria-activedescendant")
        if act_id:
            try:
                el = driver.find_element(By.ID, act_id)
                if el.is_displayed():
                    return el
            except Exception:
                pass
        # fallbacks comuns
        for e in driver.find_elements(By.CSS_SELECTOR, "[role='option'][aria-selected='true'], .active, li[aria-selected='true']"):
            if e.is_displayed():
                return e
    except Exception:
        pass
    return None


def _collect_all_options_via_keyboard(driver, css: str = None, xpath: str = None, max_steps: int = 1200, pause: float = 0.08, max_duration: float = 60.0) -> list:
    """
    Abre o dropdown, vai ao topo e percorre TODAS as opções com ARROW_DOWN,
    capturando os textos exibidos (funciona em listas virtualizadas).
    """
    LOG.info("Coletando opções via teclado (ARIA navigation)...")
    input_el = _open_dropdown_via_button(driver, css, xpath)
    container = _find_listbox_container(driver)
    try:
        # garante estar no topo
        if container:
            driver.execute_script("arguments[0].scrollTop = 0;", container)
    except Exception:
        pass

    try:
        input_el.click()
    except Exception:
        pass

    # posiciona na primeira opção
    try:
        input_el.send_keys(Keys.HOME)
    except Exception:
        pass
    time.sleep(0.05)
    try:
        input_el.send_keys(Keys.ARROW_DOWN)
    except Exception:
        pass
    time.sleep(pause)

    seen_list, seen_set = [], set()
    stagnant = 0

    t0 = time.time()
    for _ in range(max_steps):
        if time.time() - t0 > max_duration:
            LOG.warning("Encerrado por tempo (max_duration) na coleta via teclado.")
            break
        text = ""
        opt = _get_active_option(driver, input_el)
        if opt:
            try:
                text = opt.text.strip()
            except Exception:
                text = ""
        if not text:
            # alguns componentes espelham o texto na value
            try:
                text = (input_el.get_attribute("value") or "").strip()
            except Exception:
                text = ""

        if text and "SELECIONE" not in text.upper() and text not in seen_set:
            seen_set.add(text)
            seen_list.append(text)
            stagnant = 0
        else:
            stagnant += 1

        # avança para o próximo
        try:
            input_el.send_keys(Keys.ARROW_DOWN)
        except Exception:
            pass
        time.sleep(pause)

        # critério de parada: fim do scroll e sem itens novos em algumas iterações
        if container:
            try:
                top = driver.execute_script("return arguments[0].scrollTop;", container)
                ch  = driver.execute_script("return arguments[0].clientHeight;", container)
                sh  = driver.execute_script("return arguments[0].scrollHeight;", container)
                if (top + ch + 3) >= sh and stagnant >= 3:
                    break
            except Exception:
                pass
        if stagnant >= 18:
            LOG.debug("Sem novidades por várias iterações; encerrando varredura via teclado.")
            break

    LOG.info(f"Total coletado (teclado): {len(seen_list)}")
    return seen_list


def _collect_all_options_via_aria(driver, css: str = None, xpath: str = None, max_guard: int = 2000, pause: float = 0.06, max_duration: float = 60.0) -> list:
    """Percorre TODAS as opções usando aria-activedescendant (funciona com listas virtualizadas)."""
    LOG.info("Coletando opções via aria-activedescendant...")
    input_el = _open_dropdown_via_button(driver, css, xpath)

    # vai ao topo e ativa o primeiro item
    try:
        input_el.click()
        input_el.send_keys(Keys.HOME)
        time.sleep(0.05)
        input_el.send_keys(Keys.ARROW_DOWN)
    except Exception:
        pass

    seen_texts: list[str] = []
    last_id = None
    last_index = -1
    stall = 0

    def parse_index(act_id: str) -> int:
        try:
            return int(re.search(r"(\d+)$", act_id).group(1))
        except Exception:
            return -1

    t0 = time.time()
    for _ in range(max_guard):
        if time.time() - t0 > max_duration:
            LOG.warning("Encerrado por tempo (max_duration) na coleta via aria.")
            break
        act_id = ""
        try:
            act_id = input_el.get_attribute("aria-activedescendant") or ""
        except Exception:
            pass

        if act_id and act_id != last_id:
            # pega o texto do item ativo
            try:
                opt = driver.find_element(By.ID, act_id)
                txt = (opt.text or "").strip()
            except Exception:
                txt = (input_el.get_attribute("value") or "").strip()

            if txt and "SELECIONE" not in txt.upper() and txt not in seen_texts:
                seen_texts.append(txt)

            # detecta wrap (índice caiu)
            idx = parse_index(act_id)
            if last_index != -1 and idx != -1 and idx < last_index:
                break
            last_index = idx
            last_id = act_id
            stall = 0
        else:
            stall += 1

        # desce 1
        try:
            input_el.send_keys(Keys.ARROW_DOWN)
        except Exception:
            pass
        time.sleep(pause)

        # fim físico + sem novidades => parar
        container = _find_listbox_container(driver)
        at_end = False
        if container:
            try:
                top = driver.execute_script("return arguments[0].scrollTop;", container)
                ch  = driver.execute_script("return arguments[0].clientHeight;", container)
                sh  = driver.execute_script("return arguments[0].scrollHeight;", container)
                at_end = (top + ch + 2) >= sh
            except Exception:
                pass
        if at_end and stall >= 10:
            LOG.debug("Chegou ao fim do container (aria) sem novidades; encerrando.")
            break
    LOG.info(f"Total coletado (aria): {len(seen_texts)}")
    return seen_texts


def _collect_cnpjs_via_js(driver) -> list:
    """Usa JS no contexto da página para varrer todo o listbox do #cnpjRaiz e retornar TODOS os textos."""
    js = r"""
    const done = arguments[0];
    (async () => {
      const sleep = (ms) => new Promise(r => setTimeout(r, ms));
      try {
        const input = document.getElementById("cnpjRaiz");
        if (!input) return done({error:"input #cnpjRaiz não encontrado"});

        // Abre a lista (clica no botão irmão)
        try { input.click(); } catch(e){}
        try {
          var btn = input.parentElement && input.parentElement.querySelector("button");
          if (btn) btn.click();
        } catch(e){}
        await sleep(150);

        const popupId = input.getAttribute("aria-owns") || input.getAttribute("aria-controls") || "cnpjRaiz-popup";
        const listbox = document.getElementById(popupId) ||
                        document.querySelector('#cnpjRaiz-popup,[role="listbox"][aria-labelledby="cnpjRaiz-label"]');
        if (!listbox) return done({error:"listbox não encontrado"});

        // Encontra o container com maior overflow (onde o scroll realmente acontece)
        const findScrollable = (root) => {
          let best = root, bestOv = root.scrollHeight - root.clientHeight;
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
          while (walker.nextNode()) {
            const el = walker.currentNode;
            const ov = el.scrollHeight - el.clientHeight;
            if (ov > bestOv + 4) { best = el; bestOv = ov; }
          }
          return best;
        };
        const scroller = findScrollable(listbox);

        const getOptionTexts = () =>
          Array.from(listbox.querySelectorAll('[role="option"], [id^="cnpjRaiz-option-"]'))
               .map(el => (el.textContent || "").trim())
               .filter(Boolean);

        const seen = new Set();
        const addVisible = () => getOptionTexts().forEach(t => { if (t && !/SELECIONE/i.test(t)) seen.add(t); });

        // Varrer descendo (com limite de tempo)
        const deadline = Date.now() + 90000; // 90s hard limit
        addVisible();
        let lastSize = -1, stagnant = 0;
        const step = Math.max(250, Math.floor(scroller.clientHeight));
        for (let guard = 0; guard < 2000; guard++) {
          if (Date.now() > deadline) break;
           if ((scroller.scrollTop + scroller.clientHeight) >= (scroller.scrollHeight - 2)) break;
           scroller.scrollTop = Math.min(scroller.scrollTop + step, scroller.scrollHeight);
           await sleep(45);
           addVisible();
           if (seen.size === lastSize) stagnant++; else { stagnant = 0; lastSize = seen.size; }
           if (stagnant >= 10) break;
         }
 
        // Passada de segurança voltando ao topo
        for (let guard = 0; guard < 1000; guard++) {
          if (Date.now() > deadline) break;
           if (scroller.scrollTop <= 0) break;
           scroller.scrollTop = Math.max(scroller.scrollTop - step, 0);
           await sleep(35);
           addVisible();
         }
 
         return done(Array.from(seen));
       } catch (e) {
         return done({error: String(e)});
       }
    })();
    """
    try:
        data = driver.execute_async_script(js)
        if isinstance(data, dict) and data.get("error"):
            LOG.warning(f"Coleta via JS falhou: {data.get('error')}")
            return []
        if isinstance(data, list):
            # normaliza e mantém ordem
            seen = []
            seen_set = set()
            for t in data:
                t = (t or "").strip()
                if t and t not in seen_set:
                    seen.append(t); seen_set.add(t)
            LOG.info(f"Total coletado via JS: {len(seen)}")
            return seen
    except Exception:
        pass
    return []

def collect_all_cnpjs_ano(driver, ano: str) -> list:
    """Define vigência e coleta TODOS os CNPJs (preferência: JS; fallback: ARIA)."""
    LOG.info(f"====== Vigência {ano} ======")
    set_combobox_value_by_typing(driver, X_COMBO, str(ano))
    time.sleep(SLEEP_AFTER_TYPE)  # espera solicitada

    # 1) Tenta via JS (varre o listbox inteiro no DOM)
    cnpj_list = _collect_cnpjs_via_js(driver)

    # 2) Fallback robusto via ARIA/teclado
    if not cnpj_list or len(cnpj_list) < 2:
        cnpj_list = _collect_all_options_via_aria(driver, css=CNPJ_INPUT_CSS, max_guard=2000)
        if not cnpj_list:
            _select_first_option_via_button(driver, css=CNPJ_INPUT_CSS)
            cnpj_list = _collect_all_options_via_aria(driver, css=CNPJ_INPUT_CSS, max_guard=2000)

    LOG.info(f"CNPJs coletados para {ano}: {len(cnpj_list)}")
    return cnpj_list


def _close_open_dropdowns(driver, tries: int = 3):
    """Fecha listboxes/combos abertos para não cobrir o botão Consultar."""
    for _ in range(tries):
        try:
            driver.execute_script("""
                try { document.activeElement && document.activeElement.blur(); } catch(e){}
                try { document.body && document.body.click(); } catch(e){}
            """)
        except Exception:
            pass
        try:
            # ESC ajuda a fechar o popup de opções
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            pass
        time.sleep(0.15)
        # Se ainda houver listbox visível, tenta de novo
        try:
            visible = driver.execute_script("""
                const qs = (sel) => Array.from(document.querySelectorAll(sel))
                  .filter(e => e.offsetParent !== null);
                const a = qs('[role="listbox"]');
                const b = qs('[id$="-popup"]');
                return (a.length + b.length) > 0;
            """)
            if not visible:
                return
        except Exception:
            return

def _find_consultar_button(driver):
    """Localiza o botão Consultar por vários seletores robustos."""
    candidates = [
        (By.XPATH, X_BTN_CONSULTA),
        (By.XPATH, "//button[normalize-space()='Consultar']"),
        (By.XPATH, "//button[contains(@class,'br-button')][contains(normalize-space(.),'Consultar')]"),
        (By.CSS_SELECTOR, "button.br-button"),
    ]
    for by, sel in candidates:
        try:
            els = driver.find_elements(by, sel)
            for el in els:
                if el.is_displayed():
                    return el
        except Exception:
            continue
    return None

def _wait_button_enabled(driver, timeout: int = 25):
    """Espera o botão Consultar existir, estar visível e não estar 'disabled'."""
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout:
        btn = _find_consultar_button(driver)
        if btn:
            try:
                aria_dis = (btn.get_attribute("aria-disabled") or "").lower() in ("true", "1")
                dis = btn.get_attribute("disabled") is not None
                if btn.is_displayed() and (not aria_dis) and (not dis):
                    return btn
            except Exception as e:
                last_err = e
        time.sleep(0.15)
    if last_err:
        raise TimeoutException(str(last_err))
    raise TimeoutException("Botão Consultar não ficou habilitado/visível a tempo.")

def _element_not_covered(driver, el) -> bool:
    """Confere se o elemento não está coberto por overlay no ponto central."""
    try:
        rect = driver.execute_script("""
            const r = arguments[0].getBoundingClientRect();
            return {x: (r.left + r.right)/2, y: (r.top + r.bottom)/2};
        """, el)
        topEl = driver.execute_script("return document.elementFromPoint(arguments[0].x, arguments[0].y);", rect)
        return topEl is not None and (topEl == el or el.contains(topEl))
    except Exception:
        return True

def click_consultar(driver, timeout: int = 30) -> bool:
    """Fecha dropdowns, espera o botão habilitar e clica com retry e JS fallback."""
    for attempt in range(3):
        LOG.info(f"Clicando em Consultar (tentativa {attempt+1}/3)...")
        _close_open_dropdowns(driver, tries=2)

        try:
            btn = _wait_button_enabled(driver, timeout=max(10, timeout - attempt*5))
        except TimeoutException:
            LOG.warning("Tempo esgotado esperando botão habilitar.")
            continue

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        except Exception:
            pass

        if not _element_not_covered(driver, btn):
            LOG.info("Botão coberto por overlay; fechando dropdowns e tentando novamente.")
            _close_open_dropdowns(driver, tries=2)

        try:
            btn.click()
            return True
        except (ElementClickInterceptedException, StaleElementReferenceException):
            LOG.info("Click interceptado/stale; tentando JS click.")
            try:
                driver.execute_script("arguments[0].click();", btn)
                return True
            except Exception:
                time.sleep(0.3)
                continue
        except Exception as e:
            LOG.warning(f"Falha ao clicar: {e}")
            time.sleep(0.3)

    return False

def consultar_para_todos(driver, anos=("2025", "2026")):
    for ano in anos:
        cnpj_list = collect_all_cnpjs_ano(driver, str(ano))

        for cnpj in cnpj_list:
            LOG.info(f"[{ano}] CNPJ => {cnpj}")
            # Seleciona CNPJ
            if not _type_select(driver, cnpj, css=CNPJ_INPUT_CSS):
                if not _select_option_by_text_via_button(driver, cnpj, css=CNPJ_INPUT_CSS):
                    LOG.warning(f"Falha ao selecionar CNPJ: {cnpj}")
                    continue

            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, X_ESTABELECIMENTOS)))
            time.sleep(SLEEP_AFTER_TYPE)

            estab_list = _collect_all_options_via_keyboard(driver, xpath=X_ESTABELECIMENTOS, max_duration=45.0)
            if not estab_list:
                _select_first_option_via_button(driver, xpath=X_ESTABELECIMENTOS)
                estab_list = _collect_all_options_via_keyboard(driver, xpath=X_ESTABELECIMENTOS, max_duration=45.0)
            LOG.info(f"[{ano}] Estabelecimentos detectados: {len(estab_list)}")

            for estab in estab_list:
                LOG.info(f"[{ano}] {cnpj} -> Estabelecimento => {estab}")
                if not _type_select(driver, estab, xpath=X_ESTABELECIMENTOS):
                    _select_option_by_text_via_button(driver, estab, xpath=X_ESTABELECIMENTOS)
                time.sleep(SLEEP_AFTER_TYPE)

                # FECHA DROPDOWNS E CLICA COM RETRY
                if not click_consultar(driver, timeout=30):
                    LOG.error("Não consegui clicar em Consultar; avançando para o próximo.")
                    continue

                LOG.info("Clique em Consultar realizado; aguardando 5s...")
                time.sleep(SLEEP_AFTER_CONSULT)

                try:
                    WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.XPATH, X_INFO_ROOT)))
                except Exception:
                    time.sleep(0.8)

                row = extract_result_data(driver, ano)
                append_row_to_excel(
                    path="relatorio_fap.xlsx",
                    row=row,
                    headers=[
                        "CNPJ_Raiz","Razao_Social","CNPJ_Estab","Estab_Nome",
                        "UF","Municipio","Vigencia","Aliquota","Data_Consulta",
                    ],
                )

def main():
    # Valida IPs "pinnados" antes de automatizar (evita surpresas)
    if VALIDATE_IPS_BEFORE and BIND_DEST_IPS:
        validate_host_ip_map_or_fail(BIND_DEST_IPS)

    driver = start_brave_with_active_profile(
        host_ip_map=None,          # pinagem já vem do ex_brave.bat
        proxy_url=None,            # sem proxy
        keep_open=KEEP_OPEN,
        attach_debugger=ATTACH_DEBUGGER  # anexa no Brave aberto em 127.0.0.1:9222
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

        # Só fecha o navegador se NÃO quiser manter aberto
        try:
            if not KEEP_OPEN:
                driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
