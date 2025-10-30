from pathlib import Path
from typing import Optional, Dict
import os, json, urllib.request  # <- add
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# Configurações do browser (apenas o que é de navegador)
PROFILE_DIR_OVERRIDE: Optional[str] = "Pessoal"
CLOSE_BRAVE_FIRST = False
KEEP_OPEN = True
# Anexar no Brave já aberto pelo ex_brave.bat
ATTACH_DEBUGGER: Optional[str] = "127.0.0.1:9222"
PROXY_URL: Optional[str] = None  # sem proxy


def brave_path() -> str:
    for p in [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]:
        if os.path.exists(p):
            return p
    return r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"


def brave_user_data_dir() -> Path:
    return Path(os.getenv("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "User Data"


def _devtools_ready(addr: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{addr}/json/version", timeout=timeout) as r:
            _ = json.loads(r.read().decode("utf-8", "ignore"))
            return True
    except Exception:
        return False


def start_brave_with_active_profile(
    host_ip_map: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None,
    keep_open: bool = True,
    attach_debugger: Optional[str] = None,
) -> webdriver.Chrome:
    opts = Options()

    if attach_debugger:
        if not _devtools_ready(attach_debugger, timeout=3.0):
            raise RuntimeError(
                f"DevTools fora do ar em {attach_debugger}. Abra o Brave via launcher_ip\\ex_brave.bat e tente de novo."
            )
        opts.add_experimental_option("debuggerAddress", attach_debugger)
    else:
        # Abre uma nova instância (não é o fluxo atual, mas deixo pronto)
        opts.binary_location = brave_path()
        user_data = str(brave_user_data_dir())
        profile_dir = PROFILE_DIR_OVERRIDE or "Default"
        opts.add_argument(f"--user-data-dir={user_data}")
        opts.add_argument(f"--profile-directory={profile_dir}")
        if host_ip_map:
            rules = ",".join([f"MAP {h} {ip}" for h, ip in host_ip_map.items()] + ["EXCLUDE localhost"])
            opts.add_argument(f"--host-resolver-rules={rules}")
        if proxy_url:
            opts.add_argument(f"--proxy-server={proxy_url}")

    # Cria o driver conectando ao DevTools/Brave
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver
