"""Motor RTP RFC 3550 para streaming de audio bidireccional de baja latencia con códecs G.711."""

import audioop
import os
import socket
import struct
import threading
import time
from typing import Callable, Optional

class RTPAudioEngine:
    def __init__(self, bind_ip: str, local_port: int = 16384,
                 codec: str = "PCMU",
                 on_log: Optional[Callable[[str], None]] = None,
                 on_audio_received: Optional[Callable[[bytes], None]] = None):
        self.bind_ip = bind_ip
        self.local_port = local_port
        self.codec = codec.upper()  # PCMU o PCMA
        self.payload_type = 0 if self.codec == "PCMU" else 8
        self.on_log = on_log or (lambda msg: None)
        self.on_audio_received = on_audio_received or (lambda pcm: None)

        self.remote_ip: Optional[str] = None
        self.remote_port: Optional[int] = None

        self._sock: Optional[socket.socket] = None
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None

        # RTP header state
        self.ssrc = struct.unpack("!I", os.urandom(4))[0]
        self.seq_num = 0
        self.timestamp = 0

        # Stats
        self.packets_sent = 0
        self.packets_recv = 0
        self.bytes_sent = 0
        self.bytes_recv = 0

    def set_remote_endpoint(self, ip: str, port: int, codec_pt: int = 0):
        self.remote_ip = ip
        self.remote_port = port
        if codec_pt in (0, 8):
            self.payload_type = codec_pt
            self.codec = "PCMU" if codec_pt == 0 else "PCMA"
        self.on_log(f"[RTP] Endpoint remoto establecido en {ip}:{port} (PT={self.payload_type} - {self.codec})")

    def start(self):
        if self._running:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.bind_ip, self.local_port))
            self._sock.settimeout(0.5)
            self._running = True

            self.packets_sent = 0
            self.packets_recv = 0
            self.bytes_sent = 0
            self.bytes_recv = 0

            self._recv_thread = threading.Thread(target=self._receive_loop, name="RTP-Recv", daemon=True)
            self._recv_thread.start()
            self.on_log(f"[RTP] Socket activo en {self.bind_ip}:{self.local_port}")
        except Exception as e:
            self.on_log(f"[RTP] Error iniciando socket RTP: {e}")
            self._running = False

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=1.0)
        self.on_log(f"[RTP] Stream detenido. Enviados: {self.packets_sent} pkts | Recibidos: {self.packets_recv} pkts")

    def send_pcm_frame(self, pcm_linear_16bit_8k: bytes):
        """
        Codifica un frame de 160 muestras (320 bytes a 8000Hz 16-bit mono)
        a G.711 y lo transmite en un paquete RTP de 20ms.
        """
        if not self._running or not self.remote_ip or not self.remote_port or not self._sock:
            return

        try:
            # Codificar a G.711 u-law o a-law
            if self.payload_type == 0:
                payload = audioop.lin2ulaw(pcm_linear_16bit_8k, 2)
            else:
                payload = audioop.lin2alaw(pcm_linear_16bit_8k, 2)

            # Construir encabezado RTP RFC 3550 (12 bytes)
            # V=2, P=0, X=0, CC=0 (0x80)
            b0 = 0x80
            b1 = self.payload_type & 0x7F
            self.seq_num = (self.seq_num + 1) & 0xFFFF
            self.timestamp = (self.timestamp + 160) & 0xFFFFFFFF

            header = struct.pack("!BBHII", b0, b1, self.seq_num, self.timestamp, self.ssrc)
            packet = header + payload

            self._sock.sendto(packet, (self.remote_ip, self.remote_port))
            self.packets_sent += 1
            self.bytes_sent += len(packet)
        except Exception as e:
            # Errores no fatales durante streaming
            pass

    def _receive_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
                if len(data) < 12:
                    continue

                b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
                v = (b0 >> 6) & 0x03
                if v != 2:
                    continue

                pt = b1 & 0x7F
                payload = data[12:]
                self.packets_recv += 1
                self.bytes_recv += len(data)

                # Si no teníamos el puerto remoto o cambió por NAT, fijarlo al emisor
                if not self.remote_ip:
                    self.remote_ip = addr[0]
                    self.remote_port = addr[1]

                # Decodificar payload G.711 a PCM 16-bit lineal mono 8000Hz
                if pt == 0:  # PCMU
                    pcm_data = audioop.ulaw2lin(payload, 2)
                    self.on_audio_received(pcm_data)
                elif pt == 8:  # PCMA
                    pcm_data = audioop.alaw2lin(payload, 2)
                    self.on_audio_received(pcm_data)
                elif pt == 101:  # DTMF RFC 2833 / 4733
                    pass

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.on_log(f"[RTP] Excepción en recepción: {e}")

