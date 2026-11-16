"""Servidor TFTP embebido RFC 1350 + RFC 2348 para aprovisionar el Cisco CP-8831."""

import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app.utils.xml_builder import generate_cisco_8831_xml, generate_dialplan_xml, normalize_mac

# Opcodes TFTP RFC 1350
OP_RRQ = 1
OP_WRQ = 2
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5
OP_OACK = 6

class TFTPServer:
    def __init__(self, bind_ip: str, port: int = 69, tftp_root: Optional[Path] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_phone_detected: Optional[Callable[[str, str], None]] = None,
                 get_config_func: Optional[Callable[[], dict]] = None):
        self.bind_ip = bind_ip
        self.port = port
        self.tftp_root = tftp_root or Path("tftp_root")
        self.fallback_root = Path("cisco_tftp_example/tftp_files")
        self.on_log = on_log or (lambda msg: None)
        self.on_phone_detected = on_phone_detected or (lambda mac, ip: None)
        self.get_config_func = get_config_func or (lambda: {})
        
        self._running = False
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def log(self, message: str):
        self.on_log(f"[TFTP] {message}")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_server, name="TFTP-Server", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.log("Servidor TFTP detenido")

    def _run_server(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.bind_ip, self.port))
            self._sock.settimeout(1.0)
            self.log(f"Servidor TFTP activo escuchando en {self.bind_ip}:{self.port}")
        except Exception as e:
            self.log(f"Error iniciando socket TFTP en {self.bind_ip}:{self.port} - {e}")
            self._running = False
            return

        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
                if len(data) < 4:
                    continue
                opcode = struct.unpack("!H", data[:2])[0]
                if opcode == OP_RRQ:
                    # Iniciar hilo de transferencia para atender la petición
                    t = threading.Thread(
                        target=self._handle_rrq,
                        args=(data, addr),
                        daemon=True
                    )
                    t.start()
                elif opcode == OP_WRQ:
                    self._send_error_raw(addr, 2, "Escritura no permitida")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.log(f"Excepción en ciclo principal: {e}")

    def _parse_rrq(self, data: bytes):
        parts = data[2:].split(b"\x00")
        filename = parts[0].decode("utf-8", errors="replace").strip()
        options = {}
        i = 2
        while i + 1 < len(parts):
            try:
                opt_name = parts[i].decode("utf-8", errors="ignore").lower()
                opt_val = parts[i + 1].decode("utf-8", errors="ignore")
                if opt_name:
                    options[opt_name] = opt_val
            except Exception:
                pass
            i += 2
        return filename, options

    def _handle_rrq(self, data: bytes, client_addr: tuple):
        chan = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Bind en puerto efímero con la misma IP
            chan.bind((self.bind_ip, 0))
        except Exception:
            chan.bind(("0.0.0.0", 0))

        try:
            filename, options = self._parse_rrq(data)
            clean_name = filename.strip("/\\")
            self.log(f"Petición de {client_addr[0]}:{client_addr[1]} -> '{clean_name}'")

            # Detectar teléfono si solicita SEP<MAC>.cnf.xml
            upper_name = clean_name.upper()
            if upper_name.startswith("SEP") and ".CNF.XML" in upper_name:
                mac_part = upper_name.replace("SEP", "").split(".")[0]
                if len(mac_part) == 12:
                    self.on_phone_detected(mac_part, client_addr[0])

            payload = self._resolve_file_content(clean_name, client_addr[0])
            if payload is None:
                self.log(f"Archivo no encontrado: '{clean_name}'")
                self._send_error(chan, client_addr, 1, f"Archivo no encontrado: {clean_name}")
                return

            blksize = 512
            if "blksize" in options:
                try:
                    requested_blk = int(options["blksize"])
                    blksize = max(512, min(requested_blk, 1468))
                except ValueError:
                    pass

            # Si hay opciones, enviar OACK
            if options:
                oack_opts = {}
                if "blksize" in options:
                    oack_opts["blksize"] = str(blksize)
                if "tsize" in options:
                    oack_opts["tsize"] = str(len(payload))

                oack_pkt = struct.pack("!H", OP_OACK)
                for k, v in oack_opts.items():
                    oack_pkt += k.encode("ascii") + b"\x00" + v.encode("ascii") + b"\x00"

                chan.sendto(oack_pkt, client_addr)
                chan.settimeout(3.0)
                try:
                    resp, _ = chan.recvfrom(1024)
                    resp_op = struct.unpack("!H", resp[:2])[0]
                    if resp_op != OP_ACK:
                        return
                except socket.timeout:
                    return

            self._transfer_payload(chan, client_addr, payload, blksize)
            self.log(f"Transferencia completada: '{clean_name}' ({len(payload)} bytes)")

        except Exception as e:
            self.log(f"Error procesando '{clean_name}': {e}")
        finally:
            chan.close()

    def _resolve_file_content(self, filename: str, client_ip: str) -> Optional[bytes]:
        """Resuelve archivos dinámicos o estáticos en memoria/disco."""
        fn_upper = filename.upper()
        cfg = self.get_config_func()

        # Generación dinámica de SEPMAC.cnf.xml
        if fn_upper.startswith("SEP") and (fn_upper.endswith(".CNF.XML") or fn_upper.endswith(".CNF.XML.SGN")):
            mac = fn_upper.replace("SEP", "").split(".")[0]
            xml_text = generate_cisco_8831_xml(
                mac=mac,
                pc_ip=self.bind_ip,
                sip_port=cfg.get("sip_port", 5060),
                extension=cfg.get("extension", "8831"),
                display_name=cfg.get("display_name", "PC Handsfree"),
                auto_answer=cfg.get("auto_answer", True),
                transport=cfg.get("transport", "1")
            )
            return xml_text.encode("utf-8")

        # XMLDefault.cnf.xml
        if "XMLDEFAULT" in fn_upper:
            xml_text = generate_cisco_8831_xml(
                mac="000000000000",
                pc_ip=self.bind_ip,
                sip_port=cfg.get("sip_port", 5060),
                extension=cfg.get("extension", "8831"),
                display_name=cfg.get("display_name", "PC Handsfree"),
                auto_answer=cfg.get("auto_answer", True),
                transport=cfg.get("transport", "1")
            )
            return xml_text.encode("utf-8")

        # dialplan.xml
        if fn_upper == "DIALPLAN.XML" or fn_upper == "DIALPLAN.XML.SGN":
            return generate_dialplan_xml().encode("utf-8")

        # Buscar en tftp_root
        local_path = self.tftp_root / filename
        if local_path.is_file():
            return local_path.read_bytes()

        # Buscar en fallback_root
        fallback_path = self.fallback_root / filename
        if fallback_path.is_file():
            return fallback_path.read_bytes()

        # Buscar insensitive en fallback
        if self.fallback_root.exists():
            for f in self.fallback_root.rglob("*"):
                if f.is_file() and f.name.lower() == filename.lower():
                    return f.read_bytes()

        return None

    def _transfer_payload(self, chan: socket.socket, addr: tuple, payload: bytes, blksize: int):
        total_len = len(payload)
        offset = 0
        block = 1

        while True:
            chunk = payload[offset:offset + blksize]
            pkt = struct.pack("!HH", OP_DATA, block) + chunk

            acked = False
            for _ in range(5):
                chan.sendto(pkt, addr)
                chan.settimeout(2.5)
                try:
                    resp, _ = chan.recvfrom(1024)
                    if len(resp) >= 4:
                        op, ack_block = struct.unpack("!HH", resp[:4])
                        if op == OP_ACK and ack_block == block:
                            acked = True
                            break
                except socket.timeout:
                    continue

            if not acked:
                raise TimeoutError(f"Cliente no confirmó el bloque {block}")

            offset += blksize
            block = (block + 1) & 0xFFFF
            if len(chunk) < blksize:
                break

    def _send_error(self, sock: socket.socket, addr: tuple, code: int, message: str):
        try:
            pkt = struct.pack("!HH", OP_ERROR, code) + message.encode("utf-8", errors="replace") + b"\x00"
            sock.sendto(pkt, addr)
        except Exception:
            pass

    def _send_error_raw(self, addr: tuple, code: int, message: str):
        if self._sock:
            self._send_error(self._sock, addr, code, message)

