import json
import os
import socket
from pathlib import Path

CONFIG_FILE = Path("config.json")

DEFAULT_CONFIG = {
    "local_ip": "",
    "tftp_enabled": True,
    "tftp_port": 69,
    "sip_port": 5060,
    "rtp_port_local": 16384,
    "cisco_mac": "AC44F21AACDF",
    "extension": "8831",
    "display_name": "PC Handsfree",
    "auto_answer": True,
    "transport": "1",  # 1=TCP, 2=UDP
    "audio_input_device": "",
    "audio_output_device": "",
    "codec": "PCMU",   # PCMU (G.711u), PCMA (G.711a)
    "input_gain": 1.0,
    "output_gain": 1.0,
    "auto_connect_on_register": False,
    "log_level": "INFO"
}

def get_local_ips():
    """Detecta las direcciones IPv4 locales disponibles."""
    ips = []
    try:
        # Método para obtener la IP con ruta por defecto
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        best_ip = s.getsockname()[0]
        s.close()
        ips.append(best_ip)
    except Exception:
        pass

    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    if "127.0.0.1" not in ips:
        ips.append("127.0.0.1")
    return ips

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception as e:
            print(f"Error cargando config: {e}")

    if not cfg["local_ip"]:
        local_ips = get_local_ips()
        cfg["local_ip"] = local_ips[0] if local_ips else "127.0.0.1"

    return cfg

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error guardando config: {e}")

