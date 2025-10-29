import time
from typing import Iterable, Optional, List

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# Seletores do gov.br (exportados para uso no main)
XPATHS_CERT_BUTTON = [
    "//button[normalize-space()='Seu certificado digital']",
    "//a[normalize-space()='Seu certificado digital']",
    "//*[@role='button' and normalize-space()='Seu certificado digital']",
    "//*[self::a or self::button][contains(normalize-space(),'Certificado digital')]",
]
XPATH_ENTER_GOV = "/html/body/div/div[2]/div/div/div/div[2]/div/button[1]"
CERT_MODAL_OK_XPATH = "//button[normalize-space()='OK']"


def safe_click_any_xpath(driver, xpaths: Iterable[str], timeout: int = 20) -> bool:
    """Tenta clicar no primeiro XPath clicável, com scroll/JS fallback."""
    candidates = xpaths if isinstance(xpaths, (list, tuple, set)) else [xpaths]
    for xp in candidates:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue
    return False


def click_dom_ok_if_present(driver, timeout: int = 8):
    try:
        WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, CERT_MODAL_OK_XPATH))
        ).click()
    except Exception:
        pass


def watch_and_accept_cert_dialog(stop_event, timeout: int = 40):
    """Aceita o diálogo NATIVO do Windows do certificado (quando houver)."""
    try:
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys
    except Exception:
        return

    end = time.time() + timeout
    patterns = [
        "Selecione um certificado", "Selecionar certificado", "Select a certificate",
        "Confirmar certificado", "Escolher certificado"
    ]
    while not stop_event.is_set() and time.time() < end:
        try:
            desk = Desktop(backend="uia")
            for title in patterns:
                dlg = desk.window(title_re=f".*{title}.*")
                if dlg.exists(timeout=0.2):
                    try:
                        dlg.set_focus()
                    except Exception:
                        pass
                    for btn in ("OK", "Ok", "Continuar", "Selecionar", "Select", "Permitir"):
                        try:
                            dlg.child_window(title=btn, control_type="Button").click_input()
                            return
                        except Exception:
                            continue
                    try:
                        send_keys("{ENTER}")
                        return
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(0.4)


def has_captcha_error(driver) -> bool:
    try:
        if "Captcha inválido" in (driver.page_source or ""):
            return True
        WebDriverWait(driver, 2).until(
            EC.visibility_of_element_located((By.XPATH, "//*[contains(normalize-space(), 'Captcha inválido')]"))
        )
        return True
    except Exception:
        return False


def reset_sso_session(driver):
    try:
        driver.get("https://sso.acesso.gov.br/")
    except Exception:
        pass
    try:
        driver.delete_all_cookies()
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd(
            "Storage.clearDataForOrigin",
            {"origin": "https://sso.acesso.gov.br", "storageTypes": "all"},
        )
    except Exception:
        try:
            driver.execute_script("localStorage.clear(); sessionStorage.clear();")
        except Exception:
            pass


# ===== Helpers genéricos para combobox/dropdowns =====
def _visible_option_elements(driver):
    """Retorna elementos de opções visíveis de um dropdown (genérico para vários frameworks)."""
    xp = (
        "//*[(@role='option') and not(@aria-disabled='true')]"
        " | //mat-option[not(@disabled)]"
        " | //li[@role='option' and not(contains(@class,'disabled'))]"
        " | //div[contains(@class,'mat-option') and not(contains(@class,'disabled'))]"
    )
    els = driver.find_elements(By.XPATH, xp)
    return [e for e in els if e.is_displayed() and (e.text or '').strip()]


def open_dropdown(driver, input_xpath: str):
    """Abre o dropdown do input informado com múltiplos fallbacks de interação."""
    try:
        el = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((By.XPATH, input_xpath)))
    except Exception:
        # Fallback: presença + clique via JS
        el = WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, input_xpath)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
    try:
        el.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
        except Exception:
            # Fallback teclado: ALT+DOWN ou SPACE
            try:
                from selenium.webdriver.common.keys import Keys as _Keys
                el.send_keys(_Keys.ALT, _Keys.ARROW_DOWN)
            except Exception:
                try:
                    el.send_keys(" ")
                except Exception:
                    pass
    return el


def list_options_for_input(driver, input_xpath: str) -> List[str]:
    open_dropdown(driver, input_xpath)
    # pequena pausa para renderizar menu
    try:
        WebDriverWait(driver, 5).until(lambda d: len(_visible_option_elements(d)) > 0)
    except Exception:
        pass
    texts = [e.text.strip() for e in _visible_option_elements(driver)]
    # Remove itens vazios/placeholder
    return [t for t in texts if t and not t.lower().startswith("selecione ")]


def select_option_by_text(driver, input_xpath: str, text_exact: str) -> bool:
    open_dropdown(driver, input_xpath)
    xp = (
        f"//*[(@role='option') and normalize-space()='{text_exact}']"
        f" | //mat-option[normalize-space(.)='{text_exact}']"
        f" | //li[@role='option' and normalize-space()='{text_exact}']"
        f" | //div[contains(@class,'mat-option') and normalize-space()='{text_exact}']"
    )
    try:
        opt = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xp)))
        driver.execute_script("arguments[0].scrollIntoView({block:'nearest', inline:'nearest'});", opt)
        try:
            opt.click()
        except Exception:
            driver.execute_script("arguments[0].click();", opt)
        return True
    except Exception:
        return False


def set_combobox_value_by_typing(driver, input_xpath: str, value: str):
    el = open_dropdown(driver, input_xpath)
    # Clear robusto
    try:
        el.clear()
    except Exception:
        pass
    try:
        from selenium.webdriver.common.keys import Keys as _Keys
        el.send_keys(_Keys.CONTROL, "a")
        el.send_keys(_Keys.BACKSPACE)
    except Exception:
        pass
    # Fallback JS
    try:
        if el.get_attribute("value"):
            driver.execute_script(
                """
                const el = arguments[0];
                el.value = '';
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                """,
                el,
            )
    except Exception:
        pass
    # Digita valor e ENTER
    try:
        from selenium.webdriver.common.keys import Keys as _Keys
        el.send_keys(value)
        el.send_keys(_Keys.ENTER)
    except Exception:
        pass


def select_first_option(driver, input_xpath: str) -> Optional[str]:
    """Abre o dropdown e seleciona a primeira opção visível não-placa (retorna o texto)."""
    open_dropdown(driver, input_xpath)
    try:
        WebDriverWait(driver, 5).until(lambda d: len(_visible_option_elements(d)) > 0)
    except Exception:
        pass
    opts = _visible_option_elements(driver)
    for opt in opts:
        txt = (opt.text or "").strip()
        if not txt or txt.lower().startswith("selecione "):
            continue
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'nearest', inline:'nearest'});", opt)
            try:
                opt.click()
            except Exception:
                driver.execute_script("arguments[0].click();", opt)
            return txt
        except Exception:
            continue
    # Fallback: seta para baixo + enter
    try:
        from selenium.webdriver.common.keys import Keys as _Keys
        inp = open_dropdown(driver, input_xpath)
        inp.send_keys(_Keys.ARROW_DOWN)
        inp.send_keys(_Keys.ENTER)
        return ""
    except Exception:
        return None
