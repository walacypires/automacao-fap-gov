import socket
import ssl
import time
from typing import Dict


def _probe_tls_http(host: str, ip: str, path: str = "/", timeout: float = 8.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED

    t0 = time.perf_counter()
    with socket.create_connection((ip, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            t1 = time.perf_counter()
            cert = ssock.getpeercert()
            req = f"HEAD {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            ssock.sendall(req.encode("ascii", "ignore"))
            data = ssock.recv(2048).decode("latin1", "ignore")
    subject = ""
    try:
        subject = ", ".join("=".join(x) for rdn in cert.get("subject", []) for x in rdn)
    except Exception:
        pass
    san = [v for (k, v) in cert.get("subjectAltName", []) if k.lower() == "dns"]
    status_line = (data.split("\r\n", 1)[0] if data else "").strip()
    return {
        "handshake_ms": round((t1 - t0) * 1000, 1),
        "subject": subject,
        "san": san,
        "status_line": status_line,
    }


def validate_host_ip_map_or_fail(host_ip_map: Dict[str, str]):
    for host, ip in host_ip_map.items():
        try:
            info = _probe_tls_http(host, ip, "/")
        except Exception as e:
            raise RuntimeError(f"[VALIDAÇÃO] Falhou conectar em {host} -> {ip}: {e}") from e
        host_in_san = any(h.lower() == host.lower() for h in info.get("san", []))
        print(f"[VALIDAÇÃO] {host} -> {ip} | handshake={info['handshake_ms']} ms | {info['status_line']}")
        print(f"           CN/SAN contem host? {'OK' if host_in_san else 'NÃO'}")
        if not host_in_san:
            raise RuntimeError(
                f"[VALIDAÇÃO] Cert apresentado por {ip} não lista {host} no SAN. SANs: {info.get('san')}"
            )
