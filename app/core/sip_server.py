"""Servidor SIP dual (TCP y UDP) y gestor de llamadas para Cisco CP-8831."""

import os
import re
import socket
import threading
import time
from typing import Callable, Dict, Optional

class SIPServer:
    def __init__(self, bind_ip: str, sip_port: int = 5060,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_phone_registered: Optional[Callable[[dict], None]] = None,
                 on_call_state_changed: Optional[Callable[[str, dict], None]] = None):
        self.bind_ip = bind_ip
        self.sip_port = sip_port
        self.on_log = on_log or (lambda msg: None)
        self.on_phone_registered = on_phone_registered or (lambda p: None)
        self.on_call_state_changed = on_call_state_changed or (lambda state, info: None)

        self._running = False
        self._udp_sock: Optional[socket.socket] = None
        self._tcp_sock: Optional[socket.socket] = None

        self._udp_thread: Optional[threading.Thread] = None
        self._tcp_listen_thread: Optional[threading.Thread] = None

        # Estado del teléfono registrado
        self.phone_info: Optional[dict] = None
        self.phone_last_seen = 0.0
        self.phone_tcp_sock: Optional[socket.socket] = None

        # Estado de la llamada activa
        self.is_in_call = False
        self.active_dialog: Optional[dict] = None
        self._local_rtp_port = 16384

    def log(self, message: str):
        self.on_log(f"[SIP] {message}")

    def start(self, local_rtp_port: int = 16384):
        if self._running:
            return
        self._local_rtp_port = local_rtp_port
        self._running = True

        # 1. Iniciar socket UDP
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.bind((self.bind_ip, self.sip_port))
            self._udp_sock.settimeout(0.5)

            self._udp_thread = threading.Thread(target=self._udp_worker, name="SIP-UDP", daemon=True)
            self._udp_thread.start()
            self.log(f"Escuchando SIP UDP en {self.bind_ip}:{self.sip_port}")
        except Exception as e:
            self.log(f"Error iniciando SIP UDP: {e}")

        # 2. Iniciar socket TCP (Recomendado por Cisco para serie 8800)
        try:
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp_sock.bind((self.bind_ip, self.sip_port))
            self._tcp_sock.listen(5)
            self._tcp_sock.settimeout(0.5)

            self._tcp_listen_thread = threading.Thread(target=self._tcp_listener, name="SIP-TCP", daemon=True)
            self._tcp_listen_thread.start()
            self.log(f"Escuchando SIP TCP en {self.bind_ip}:{self.sip_port}")
        except Exception as e:
            self.log(f"Error iniciando SIP TCP: {e}")

    def stop(self):
        self._running = False
        if self.is_in_call:
            try:
                self.hangup_call()
            except Exception:
                pass

        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
        if self._tcp_sock:
            try:
                self._tcp_sock.close()
            except Exception:
                pass

        self.log("Servidor SIP detenido")

    # ==========================
    # MANEJO DE PROTOCOLO SIP
    # ==========================

    def _udp_worker(self):
        while self._running:
            try:
                data, addr = self._udp_sock.recvfrom(8192)
                msg_text = data.decode("utf-8", errors="replace")
                self._process_sip_message(msg_text, addr, transport="UDP", sock=self._udp_sock)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.log(f"Excepción en SIP UDP: {e}")

    def _tcp_listener(self):
        while self._running:
            try:
                client_sock, addr = self._tcp_sock.accept()
                t = threading.Thread(target=self._tcp_client_worker, args=(client_sock, addr), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    pass

    def _tcp_client_worker(self, client_sock: socket.socket, addr: tuple):
        client_sock.settimeout(120.0)
        buffer = ""
        while self._running:
            try:
                data = client_sock.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")

                # Parsear mensajes delimitados por Content-Length
                while "\r\n\r\n" in buffer:
                    buffer = buffer.lstrip("\r\n ")
                    if not buffer:
                        break
                    header_part, body_candidate = buffer.split("\r\n\r\n", 1)
                    cl_match = re.search(r"Content-Length:\s*(\d+)", header_part, re.IGNORECASE)
                    content_len = int(cl_match.group(1)) if cl_match else 0

                    body_bytes = body_candidate.encode("utf-8")
                    if len(body_bytes) >= content_len:
                        body_actual = body_bytes[:content_len].decode("utf-8", errors="replace")
                        msg_text = header_part + "\r\n\r\n" + body_actual
                        buffer = body_bytes[content_len:].decode("utf-8", errors="replace")
                        self._process_sip_message(msg_text, addr, transport="TCP", sock=client_sock)
                    else:
                        break
            except Exception:
                break
        if self.phone_tcp_sock == client_sock:
            self.phone_tcp_sock = None
        try:
            client_sock.close()
        except Exception:
            pass

    def _process_sip_message(self, msg_text: str, addr: tuple, transport: str, sock: socket.socket):
        lines = msg_text.splitlines()
        if not lines:
            return

        first_line = lines[0].strip()
        headers = self._parse_headers(lines[1:])
        body = msg_text.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in msg_text else ""

        # Identificar si es Request o Response
        if first_line.startswith("SIP/2.0"):
            self._handle_response(first_line, headers, body, addr, transport, sock)
        else:
            method = first_line.split(" ")[0].upper()
            if method == "REGISTER":
                self._handle_register(first_line, headers, addr, transport, sock)
            elif method == "INVITE":
                self._handle_incoming_invite(first_line, headers, body, addr, transport, sock)
            elif method == "ACK":
                self._handle_ack(first_line, headers, body)
            elif method == "BYE":
                self._handle_bye(first_line, headers, addr, transport, sock)
            elif method == "CANCEL":
                self._handle_cancel(first_line, headers, addr, transport, sock)
            elif method == "OPTIONS":
                self._handle_options(first_line, headers, addr, transport, sock)

    def _parse_headers(self, header_lines: list) -> Dict[str, str]:
        headers = {}
        for line in header_lines:
            if not line or ":" not in line:
                continue
            key, val = line.split(":", 1)
            k = key.strip()
            # Mapear headers compactos SIP
            if k.lower() == "v":
                k = "Via"
            elif k.lower() == "f":
                k = "From"
            elif k.lower() == "t":
                k = "To"
            elif k.lower() == "m":
                k = "Contact"
            elif k.lower() == "c":
                k = "Content-Type"
            elif k.lower() == "l":
                k = "Content-Length"
            elif k.lower() == "i":
                k = "Call-ID"

            headers[k] = val.strip()
        return headers

    def _handle_register(self, first_line: str, headers: dict, addr: tuple, transport: str, sock: socket.socket):
        contact = headers.get("Contact", "")
        user_agent = headers.get("User-Agent", "Cisco IP Phone")
        from_hdr = headers.get("From", "")

        ext_match = re.search(r"sip:([^@]+)@", from_hdr)
        ext = ext_match.group(1) if ext_match else "8831"

        port = addr[1]
        c_match = re.search(r"sip:[^@]+@[^:]+:(\d+)", contact)
        if c_match:
            port = int(c_match.group(1))

        self.phone_info = {
            "ip": addr[0],
            "port": 5060,  # Puerto estándar SIP del teléfono
            "client_port": addr[1],
            "transport": transport,
            "extension": ext,
            "user_agent": user_agent,
            "registered_at": time.time()
        }
        if transport == "TCP":
            self.phone_tcp_sock = sock
        self.phone_last_seen = time.time()
        self.log(f"Teléfono registrado: Ext {ext} desde {addr[0]}:{port} vía {transport} ({user_agent})")
        self.on_phone_registered(self.phone_info)

        # Responder 200 OK
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')};tag={os.urandom(4).hex()}\r\n"
            f"Call-ID: {headers.get('Call-ID', '')}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Contact: <sip:{ext}@{addr[0]}:{port}>;expires=3600\r\n"
            f"Expires: 3600\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_raw(resp, addr, transport, sock)

    def _handle_options(self, first_line: str, headers: dict, addr: tuple, transport: str, sock: socket.socket):
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')};tag={os.urandom(4).hex()}\r\n"
            f"Call-ID: {headers.get('Call-ID', '')}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Allow: INVITE, ACK, CANCEL, BYE, NOTIFY, OPTIONS, REFER\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_raw(resp, addr, transport, sock)

    def _handle_incoming_invite(self, first_line: str, headers: dict, body: str, addr: tuple, transport: str, sock: socket.socket):
        self.log(f"Llamada entrante iniciada desde el teléfono {addr[0]}")

        # Extraer SDP remoto del Cisco
        remote_rtp = self._parse_sdp(body)
        tag = os.urandom(4).hex()
        call_id = headers.get("Call-ID", "")

        self.active_dialog = {
            "call_id": call_id,
            "remote_tag": re.search(r"tag=([a-zA-Z0-9_-]+)", headers.get("From", "")).group(1) if "tag=" in headers.get("From", "") else "",
            "local_tag": tag,
            "remote_uri": headers.get("Contact", f"sip:{addr[0]}:{addr[1]}").strip("<>"),
            "remote_ip": remote_rtp.get("ip", addr[0]),
            "remote_rtp_port": remote_rtp.get("port", 16384),
            "codec_pt": remote_rtp.get("pt", 0),
            "transport": transport,
            "addr": addr,
            "cseq_num": int(headers.get("CSeq", "1 INVITE").split(" ")[0])
        }

        # 1. Enviar 100 Trying
        trying = (
            f"SIP/2.0 100 Trying\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_raw(trying, addr, transport, sock)

        # 2. Generar SDP local
        sdp = self._generate_sdp(self.bind_ip, self._local_rtp_port)
        sdp_bytes = sdp.encode("utf-8")

        # 3. Enviar 200 OK con SDP
        ok_resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')};tag={tag}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Contact: <sip:handsfree@{self.bind_ip}:{self.sip_port}>\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp_bytes)}\r\n\r\n"
            f"{sdp}"
        )
        self._send_raw(ok_resp, addr, transport, sock)

        self.is_in_call = True
        self.log(f"Llamada aceptada. Audio RTP hacia {self.active_dialog['remote_ip']}:{self.active_dialog['remote_rtp_port']}")
        self.on_call_state_changed("connected", self.active_dialog)

    def _handle_ack(self, first_line: str, headers: dict, body: str):
        self.log("ACK recibido. Canal de audio establecido.")

    def _handle_bye(self, first_line: str, headers: dict, addr: tuple, transport: str, sock: socket.socket):
        self.log("Petición BYE recibida. Finalizando llamada.")
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')}\r\n"
            f"Call-ID: {headers.get('Call-ID', '')}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_raw(resp, addr, transport, sock)
        self.is_in_call = False
        self.active_dialog = None
        self.on_call_state_changed("disconnected", {"reason": "Remoto colgó"})

    def _handle_cancel(self, first_line: str, headers: dict, addr: tuple, transport: str, sock: socket.socket):
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')}\r\n"
            f"Call-ID: {headers.get('Call-ID', '')}\r\n"
            f"CSeq: {headers.get('CSeq', '')}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_raw(resp, addr, transport, sock)
        self.is_in_call = False
        self.active_dialog = None
        self.on_call_state_changed("disconnected", {"reason": "Llamada cancelada"})

    def _handle_response(self, first_line: str, headers: dict, body: str, addr: tuple, transport: str, sock: Optional[socket.socket] = None):
        code_str = first_line.split(" ")[1]
        code = int(code_str) if code_str.isdigit() else 0

        self.log(f"Respuesta SIP recibida: {first_line}")

        if code == 200 and "INVITE" in headers.get("CSeq", ""):
            # Cisco aceptó nuestra llamada por altavoz (AutoAnswer)
            remote_rtp = self._parse_sdp(body)
            to_header = headers.get("To", "")
            
            if self.active_dialog:
                self.active_dialog["remote_ip"] = remote_rtp.get("ip", addr[0])
                self.active_dialog["remote_rtp_port"] = remote_rtp.get("port", 16384)
                self.active_dialog["codec_pt"] = remote_rtp.get("pt", 0)

                # Enviar ACK
                ack_pkt = (
                    f"ACK {self.active_dialog['remote_uri']} SIP/2.0\r\n"
                    f"Via: SIP/2.0/{transport} {self.bind_ip}:{self.sip_port};branch=z9hG4bK{os.urandom(4).hex()}\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"From: <sip:handsfree@{self.bind_ip}:{self.sip_port}>;tag={self.active_dialog['local_tag']}\r\n"
                    f"To: {to_header}\r\n"
                    f"Call-ID: {self.active_dialog['call_id']}\r\n"
                    f"CSeq: {self.active_dialog['cseq_num']} ACK\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self._send_to_phone(ack_pkt, sock=sock)

                self.is_in_call = True
                self.log(f"Llamada establecida exitosamente (Auto-Answer activado). RTP hacia {self.active_dialog['remote_ip']}:{self.active_dialog['remote_rtp_port']}")
                self.on_call_state_changed("connected", self.active_dialog)

    # ==========================
    # COMANDOS DE CONTROL
    # ==========================

    def start_handsfree_call(self, extension: str = "8831"):
        """Inicia una llamada hacia el Cisco CP-8831 para activar su altavoz de inmediato."""
        if not self.phone_info:
            raise RuntimeError("El teléfono Cisco aún no se ha registrado.")

        call_id = f"hf-{os.urandom(6).hex()}@{self.bind_ip}"
        local_tag = os.urandom(4).hex()
        sdp = self._generate_sdp(self.bind_ip, self._local_rtp_port)
        sdp_bytes = sdp.encode("utf-8")

        target_ip = self.phone_info["ip"]
        transport = self.phone_info.get("transport", "TCP")

        # El URI SIP de destino estándar hacia el teléfono
        remote_uri = f"sip:{extension}@{target_ip}"

        self.active_dialog = {
            "call_id": call_id,
            "local_tag": local_tag,
            "remote_tag": "",
            "remote_uri": remote_uri,
            "remote_ip": target_ip,
            "remote_rtp_port": 16384,
            "codec_pt": 0,
            "transport": transport,
            "addr": (target_ip, 5060),
            "cseq_num": 101
        }

        branch = f"z9hG4bK{os.urandom(5).hex()}"
        invite = (
            f"INVITE {remote_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/{transport} {self.bind_ip}:{self.sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: \"PC Handsfree\" <sip:handsfree@{self.bind_ip}:{self.sip_port}>;tag={local_tag}\r\n"
            f"To: <sip:{extension}@{target_ip}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 101 INVITE\r\n"
            f"Contact: <sip:handsfree@{self.bind_ip}:{self.sip_port};transport={transport.lower()}>\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp_bytes)}\r\n\r\n"
            f"{sdp}"
        )

        self.log(f"Enviando INVITE para activar altavoz de {extension} en {target_ip} vía {transport}...")
        self._send_to_phone(invite)

    def hangup_call(self):
        """Cuelga la llamada activa enviando un BYE al Cisco."""
        if not self.active_dialog:
            self.is_in_call = False
            return

        target_uri = self.active_dialog["remote_uri"]
        call_id = self.active_dialog["call_id"]
        local_tag = self.active_dialog["local_tag"]
        remote_tag = self.active_dialog.get("remote_tag", "")
        transport = self.active_dialog.get("transport", "TCP")

        self.active_dialog["cseq_num"] += 1
        cseq = self.active_dialog["cseq_num"]
        branch = f"z9hG4bK{os.urandom(5).hex()}"

        bye_pkt = (
            f"BYE {target_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/{transport} {self.bind_ip}:{self.sip_port};branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:handsfree@{self.bind_ip}:{self.sip_port}>;tag={local_tag}\r\n"
            f"To: <{target_uri}>;tag={remote_tag}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq} BYE\r\n"
            f"Content-Length: 0\r\n\r\n"
        )

        self.log("Enviando BYE para colgar llamada...")
        self._send_to_phone(bye_pkt)
        self.is_in_call = False
        self.active_dialog = None
        self.on_call_state_changed("disconnected", {"reason": "Llamada finalizada por el usuario"})

    # ==========================
    # UTILIDADES DE MENSAJERÍA
    # ==========================

    def _send_to_phone(self, msg_str: str, sock: Optional[socket.socket] = None):
        if not self.phone_info:
            return
        addr = (self.phone_info["ip"], 5060)
        transport = self.phone_info.get("transport", "TCP")
        self._send_raw(msg_str, addr, transport, sock=sock or self.phone_tcp_sock)

    def _send_raw(self, msg_str: str, addr: tuple, transport: str, sock: Optional[socket.socket] = None):
        data = msg_str.encode("utf-8")
        if transport == "UDP":
            target_sock = sock or self._udp_sock
            if target_sock:
                try:
                    target_sock.sendto(data, addr)
                except Exception as e:
                    self.log(f"Error enviando UDP a {addr}: {e}")
        else:  # TCP
            # 1. Intentar enviar primero por el socket TCP existente (conexión viva del teléfono)
            target_sock = sock or self.phone_tcp_sock
            sent = False
            if target_sock:
                try:
                    target_sock.sendall(data)
                    sent = True
                except Exception as e:
                    self.log(f"Socket TCP persistente no disponible ({e}), intentando nueva conexión...")
                    self.phone_tcp_sock = None

            # 2. Si no se pudo enviar, abrir nueva conexión TCP hacia el puerto 5060 del teléfono
            if not sent:
                target_ip = addr[0]
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(2.5)
                    s.connect((target_ip, 5060))
                    s.sendall(data)
                    self.phone_tcp_sock = s
                    sent = True
                    self.log(f"Nueva conexión TCP establecida con {target_ip}:5060")
                except Exception as e:
                    self.log(f"Error enviando TCP a {target_ip}:5060: {e}")

    def _generate_sdp(self, ip: str, rtp_port: int) -> str:
        lines = [
            "v=0",
            f"o=PCHandsfree {int(time.time())} {int(time.time())} IN IP4 {ip}",
            "s=Handsfree Audio",
            f"c=IN IP4 {ip}",
            "t=0 0",
            f"m=audio {rtp_port} RTP/AVP 0 8 101",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:8 PCMA/8000",
            "a=rtpmap:101 telephone-event/8000",
            "a=fmtp:101 0-15",
            "a=sendrecv",
            "a=ptime:20"
        ]
        return "\r\n".join(lines) + "\r\n"

    def _parse_sdp(self, sdp_text: str) -> dict:
        info = {"ip": self.bind_ip, "port": 16384, "pt": 0}
        for line in sdp_text.splitlines():
            line = line.strip()
            if line.startswith("c=IN IP4"):
                parts = line.split(" ")
                if len(parts) >= 3:
                    info["ip"] = parts[2].strip()
            elif line.startswith("m=audio"):
                parts = line.split(" ")
                if len(parts) >= 4:
                    try:
                        info["port"] = int(parts[1])
                        info["pt"] = int(parts[3])
                    except ValueError:
                        pass
        return info

