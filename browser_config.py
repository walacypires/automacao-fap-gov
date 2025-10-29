from pathlib import Path
from typing import Optional, Dict
import re
import subprocess

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# Configurações do browser (apenas o que é de navegador)
PROFILE_DIR_OVERRIDE: Optional[str] = "Pessoal"
CLOSE_BRAVE_FIRST = False
KEEP_OPEN = True
ATTACH_DEBUGGER: Optional[str] = "127.0.0.1:9222"
PROXY_URL: Optional[str] = None  # ex.: "socks5://127.0.0.1:1080"


def brave_path() -> str:
    for p in [
        r"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
        r"C:\\Program Files (x86)\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
    ]:
        if Path(p).exists():
            return p
    return "brave.exe"


def brave_user_data_dir() -> Path:
    return Path.home() / "AppData" / "Local" / "BraveSoftware" / "Brave-Browser" / "User Data"


def get_last_used_profile(user_data_dir: Path) -> str:
    try:
        data = (user_data_dir / "Local State").read_text(encoding="utf-8")
        import json as _json
        j = _json.loads(data)
        last_used = j.get("profile", {}).get("last_used")
        if last_used:
            return last_used
    except Exception:
        pass
    return "Default"


def brave_major_version(binary: str) -> str:
    try:
        out = subprocess.check_output([binary, "--version"], text=True).strip()
        m = re.search(r"\b(\d+)\.\d+\.\d+\.\d+\b", out)  # "Brave Browser 128.0.6613.84"
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def kill_brave():
    subprocess.run(
        ["taskkill", "/F", "/IM", "brave.exe", "/T"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )


def cleanup_profile_locks(user_data_dir: Path, profile_dir: str):
    p = user_data_dir / profile_dir
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
        try:
            (p / name).unlink(missing_ok=True)
        except Exception:
            pass


def start_brave_with_active_profile(
    host_ip_map: Optional[Dict[str, str]] = None,  # reservado para uso futuro
    proxy_url: Optional[str] = None,               # reservado para uso futuro
    keep_open: bool = True,
    attach_debugger: Optional[str] = None,
) -> webdriver.Chrome:
    """
    Anexa ao Brave já aberto via DevTools (debuggerAddress) OU inicia um novo Brave com o perfil ativo.
    Esta função contém apenas configuração de navegador; a lógica de automação fica no main.
    """
    binary = brave_path()
    user_data = brave_user_data_dir()
    profile_dir = PROFILE_DIR_OVERRIDE or get_last_used_profile(user_data)

    # Modo ANEXO (recomendado): conecta num Brave já iniciado com --remote-debugging-port
    if attach_debugger:
        attach_opts = Options()
        attach_opts.binary_location = binary
        attach_opts.add_experimental_option("debuggerAddress", attach_debugger)
        driver = webdriver.Chrome(options=attach_opts)
        try:
            driver.execute_cdp_cmd(
                "Browser.grantPermissions",
                {"origin": "https://sso.acesso.gov.br", "permissions": ["geolocation"]},
            )
        except Exception:
            pass
        print(f"[perfil] Anexado ao Brave (debugger={attach_debugger}) | profile: {profile_dir}")
        return driver

    # Modo NOVA INSTÂNCIA (fallback)
    opts = Options()
    opts.binary_location = binary
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--remote-allow-origins=*")

    # Perfil
    opts.add_argument(f"--user-data-dir={user_data}")
    opts.add_argument(f"--profile-directory={profile_dir}")

    # Mantém janela aberta somente quando esta função inicia o navegador
    if keep_open:
        try:
            opts.add_experimental_option("detach", True)
        except Exception:
            pass

    # Reduz a marca de automação (apenas em nova instância)
    try:
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
    except Exception:
        pass

    # Observação: host_ip_map e proxy_url podem ser aplicados aqui futuramente
    # com --host-resolver-rules e --proxy-server, respectivamente.

    if CLOSE_BRAVE_FIRST:
        kill_brave()
        cleanup_profile_locks(user_data, profile_dir)

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception:
        major = brave_major_version(binary)
        try:
            svc = Service(ChromeDriverManager(version=major).install() if major else ChromeDriverManager().install())
            driver = webdriver.Chrome(service=svc, options=opts)
        except SessionNotCreatedException:
            kill_brave()
            cleanup_profile_locks(user_data, profile_dir)
            svc = Service(ChromeDriverManager(version=major).install() if major else ChromeDriverManager().install())
            driver = webdriver.Chrome(service=svc, options=opts)

    try:
        driver.execute_cdp_cmd(
            "Browser.grantPermissions",
            {"origin": "https://sso.acesso.gov.br", "permissions": ["geolocation"]},
        )
    except Exception:
        pass

    print(f"[perfil] Usando Brave profile: {profile_dir}")
    return driver
