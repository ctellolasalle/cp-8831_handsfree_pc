"""Ventana principal PyQt6 para el control del altavoz Cisco CP-8831 en PC."""

import datetime
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QFont, QColor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSlider, QCheckBox, QTabWidget,
    QTextEdit, QGroupBox, QGridLayout, QMessageBox, QFrame,
    QLineEdit, QDialog, QSplitter
)

from app.config import load_config, save_config, get_local_ips
from app.core.tftp_server import TFTPServer
from app.core.sip_server import SIPServer
from app.core.rtp_audio import RTPAudioEngine
from app.core.audio_bridge import AudioBridge
from app.gui.vu_meter import VUMeterWidget
from app.utils.xml_builder import generate_cisco_8831_xml, normalize_mac

class SignalBridge(QObject):
    log_signal = pyqtSignal(str)
    phone_registered_signal = pyqtSignal(dict)
    phone_detected_signal = pyqtSignal(str, str)
    call_state_signal = pyqtSignal(str, dict)
    levels_signal = pyqtSignal(float, float)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cisco CP-8831 Handsfree Audio Station")
        self.resize(960, 720)
        self.setMinimumSize(850, 600)

        self.cfg = load_config()
        self.bridge_signals = SignalBridge()

        # Componentes del Backend
        self.audio_bridge = AudioBridge(
            on_log=self._post_log,
            on_levels=self._post_levels
        )
        self.rtp_engine: Optional[RTPAudioEngine] = None
        self.sip_server: Optional[SIPServer] = None
        self.tftp_server: Optional[TFTPServer] = None

        self.call_start_time: Optional[float] = None
        self.call_timer = QTimer(self)
        self.call_timer.setInterval(1000)
        self.call_timer.timeout.connect(self._update_call_timer)

        self._setup_ui()
        self._apply_stylesheet()
        self._connect_signals()

        # Inicializar servidores y audio
        self._start_services()

    # ==========================
    # CONSTRUCCIÓN DE LA UI
    # ==========================

    def _setup_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. ENCABEZADO Y SELECTOR DE RED
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(12, 10, 12, 10)

        title_vbox = QVBoxLayout()
        title_lbl = QLabel("Cisco CP-8831 Handsfree Station")
        title_lbl.setObjectName("appTitle")
        subtitle_lbl = QLabel("Altavoz y micrófono de conferencia profesional para videollamadas")
        subtitle_lbl.setObjectName("appSubtitle")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(subtitle_lbl)
        header_layout.addLayout(title_vbox)

        header_layout.addStretch()

        ip_box = QHBoxLayout()
        ip_lbl = QLabel("IP de Red Local:")
        ip_lbl.setObjectName("boldLabel")
        self.combo_ip = QComboBox()
        self.combo_ip.setMinimumWidth(150)
        self._populate_ips()
        self.combo_ip.currentTextChanged.connect(self._on_ip_changed)

        btn_refresh_ip = QPushButton("🔄")
        btn_refresh_ip.setToolTip("Refrescar interfaces de red")
        btn_refresh_ip.clicked.connect(self._populate_ips)

        ip_box.addWidget(ip_lbl)
        ip_box.addWidget(self.combo_ip)
        ip_box.addWidget(btn_refresh_ip)
        header_layout.addLayout(ip_box)

        main_layout.addWidget(header_frame)

        # 2. CHIPS DE ESTADO
        status_bar = QHBoxLayout()
        status_bar.setSpacing(10)

        self.chip_tftp = QLabel("📁 TFTP: Iniciando...")
        self.chip_tftp.setObjectName("chipWarning")
        self.chip_sip = QLabel("☎ SIP: Iniciando...")
        self.chip_sip.setObjectName("chipWarning")
        self.chip_phone = QLabel("📱 Cisco 8831: Desconectado")
        self.chip_phone.setObjectName("chipDanger")
        self.chip_call = QLabel("⚪ Estado: En espera")
        self.chip_call.setObjectName("chipNeutral")

        status_bar.addWidget(self.chip_tftp)
        status_bar.addWidget(self.chip_sip)
        status_bar.addWidget(self.chip_phone)
        status_bar.addWidget(self.chip_call)
        status_bar.addStretch()

        main_layout.addLayout(status_bar)

        # 3. PESTAÑAS PRINCIPALES
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        # Pestaña 1: Control de Altavoz
        tab_control = QWidget()
        self._setup_control_tab(tab_control)
        self.tabs.addTab(tab_control, "🎙️ Control Manos Libres")

        # Pestaña 2: Configuración Cisco
        tab_cisco = QWidget()
        self._setup_cisco_tab(tab_cisco)
        self.tabs.addTab(tab_cisco, "⚙️ Provisión Cisco (SEP<MAC>)")

        # Pestaña 3: Logs en Vivo
        tab_logs = QWidget()
        self._setup_logs_tab(tab_logs)
        self.tabs.addTab(tab_logs, "📋 Consola de Eventos")

        main_layout.addWidget(self.tabs)

    def _setup_control_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Panel de Conexión Principal (Botón Gigante)
        call_box = QGroupBox("Control de Llamada")
        call_layout = QVBoxLayout(call_box)

        self.btn_call = QPushButton("📞 CONECTAR ALTAVOZ (INICIAR MANOS LIBRES)")
        self.btn_call.setObjectName("btnCallStart")
        self.btn_call.setFixedHeight(54)
        self.btn_call.clicked.connect(self._toggle_handsfree_call)
        call_layout.addWidget(self.btn_call)

        call_info_row = QHBoxLayout()
        self.lbl_call_timer = QLabel("⏱️ Duración: 00:00:00")
        self.lbl_call_timer.setObjectName("timerLabel")
        self.lbl_call_codec = QLabel("🔊 Códec: G.711u (PCMU 8kHz / 64 kbps)")

        self.btn_mute = QPushButton("🎙️ Silenciar Micrófono")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setObjectName("btnMute")
        self.btn_mute.clicked.connect(self._on_mute_clicked)

        call_info_row.addWidget(self.lbl_call_timer)
        call_info_row.addWidget(self.lbl_call_codec)
        call_info_row.addStretch()
        call_info_row.addWidget(self.btn_mute)
        call_layout.addLayout(call_info_row)

        layout.addWidget(call_box)

        # Panel de Dispositivos de Audio y Vúmetros
        audio_box = QGroupBox("Dispositivos de Audio de la PC")
        audio_layout = QGridLayout(audio_box)
        audio_layout.setSpacing(10)

        # Canal 1: Audio que va al Altavoz de Cisco
        lbl_in = QLabel("Sonido hacia el Altavoz del Cisco (Ej: Apps / Teams / Salida Virtual):")
        lbl_in.setObjectName("boldLabel")
        self.combo_audio_in = QComboBox()
        self.slider_spk_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_spk_vol.setRange(0, 200)
        self.slider_spk_vol.setValue(100)
        self.slider_spk_vol.valueChanged.connect(self._on_speaker_volume_changed)
        self.lbl_spk_vol_val = QLabel("100%")

        self.vu_speaker = VUMeterWidget(title="Altavoz Cisco")

        spk_vol_layout = QHBoxLayout()
        spk_vol_layout.addWidget(QLabel("Volumen:"))
        spk_vol_layout.addWidget(self.slider_spk_vol)
        spk_vol_layout.addWidget(self.lbl_spk_vol_val)

        audio_layout.addWidget(lbl_in, 0, 0)
        audio_layout.addWidget(self.combo_audio_in, 1, 0)
        audio_layout.addLayout(spk_vol_layout, 2, 0)
        audio_layout.addWidget(self.vu_speaker, 3, 0)

        # Canal 2: Micrófono del Cisco hacia la PC
        lbl_out = QLabel("Micrófono del Cisco hacia la PC (Ej: Entrada Virtual para Zoom/Teams):")
        lbl_out.setObjectName("boldLabel")
        self.combo_audio_out = QComboBox()
        self.slider_mic_gain = QSlider(Qt.Orientation.Horizontal)
        self.slider_mic_gain.setRange(0, 200)
        self.slider_mic_gain.setValue(100)
        self.slider_mic_gain.valueChanged.connect(self._on_mic_gain_changed)
        self.lbl_mic_gain_val = QLabel("100%")

        self.vu_mic = VUMeterWidget(title="Micrófono Cisco")

        mic_vol_layout = QHBoxLayout()
        mic_vol_layout.addWidget(QLabel("Ganancia:"))
        mic_vol_layout.addWidget(self.slider_mic_gain)
        mic_vol_layout.addWidget(self.lbl_mic_gain_val)

        audio_layout.addWidget(lbl_out, 0, 1)
        audio_layout.addWidget(self.combo_audio_out, 1, 1)
        audio_layout.addLayout(mic_vol_layout, 2, 1)
        audio_layout.addWidget(self.vu_mic, 3, 1)

        # Botón de refrescar dispositivos
        audio_btn_row = QHBoxLayout()
        btn_refresh_devs = QPushButton("🔄 Actualizar Dispositivos de Audio")
        btn_refresh_devs.clicked.connect(self._populate_audio_devices)
        audio_btn_row.addStretch()
        audio_btn_row.addWidget(btn_refresh_devs)

        audio_layout.addLayout(audio_btn_row, 4, 0, 1, 2)
        layout.addWidget(audio_box)

        # Guía explicativa para integración con Zoom / Teams / Meet
        tip_frame = QFrame()
        tip_frame.setObjectName("tipFrame")
        tip_layout = QVBoxLayout(tip_frame)
        tip_title = QLabel("💡 ¿Cómo usarlo con Teams, Zoom, Meet o Discord?")
        tip_title.setObjectName("boldLabel")
        tip_text = QLabel(
            "1. Instala un cable virtual como VB-Audio Cable (gratuito).\n"
            "2. En este programa: En 'Sonido hacia Altavoz', elige 'CABLE Output' (lo que reproduce la PC).\n"
            "3. En 'Micrófono hacia PC', elige 'CABLE Input' (lo que la PC escucha).\n"
            "4. En Teams/Zoom: Selecciona 'CABLE Output' como Altavoz y 'CABLE Input' como Micrófono.\n"
            "¡El hardware del Cisco CP-8831 se encargará automáticamente de la cancelación de eco!"
        )
        tip_text.setWordWrap(True)
        tip_layout.addWidget(tip_title)
        tip_layout.addWidget(tip_text)
        layout.addWidget(tip_frame)

        self._populate_audio_devices()

    def _setup_cisco_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form_box = QGroupBox("Parámetros de Aprovisionamiento Cisco CP-8831")
        form_layout = QGridLayout(form_box)
        form_layout.setSpacing(10)

        # MAC Address
        form_layout.addWidget(QLabel("Dirección MAC del Teléfono:"), 0, 0)
        self.txt_mac = QLineEdit(self.cfg.get("cisco_mac", "AC44F21AACDF"))
        self.txt_mac.setPlaceholderText("Ej: AC44F21AACDF")
        form_layout.addWidget(self.txt_mac, 0, 1)

        # Extensión
        form_layout.addWidget(QLabel("Extensión SIP:"), 1, 0)
        self.txt_ext = QLineEdit(self.cfg.get("extension", "8831"))
        form_layout.addWidget(self.txt_ext, 1, 1)

        # Nombre en pantalla
        form_layout.addWidget(QLabel("Nombre en Pantalla del Cisco:"), 2, 0)
        self.txt_display = QLineEdit(self.cfg.get("display_name", "PC Handsfree"))
        form_layout.addWidget(self.txt_display, 2, 1)

        # Puerto SIP
        form_layout.addWidget(QLabel("Puerto SIP en PC:"), 3, 0)
        self.txt_sip_port = QLineEdit(str(self.cfg.get("sip_port", 5060)))
        form_layout.addWidget(self.txt_sip_port, 3, 1)

        # Transporte
        form_layout.addWidget(QLabel("Transporte SIP Preferido:"), 4, 0)
        self.combo_transport = QComboBox()
        self.combo_transport.addItem("TCP (Recomendado Cisco)", "1")
        self.combo_transport.addItem("UDP", "2")
        if self.cfg.get("transport", "1") == "2":
            self.combo_transport.setCurrentIndex(1)
        form_layout.addWidget(self.combo_transport, 4, 1)

        # Auto Answer
        self.chk_auto_answer = QCheckBox("Auto-Respuesta en Altavoz Manos Libres (autoAnswerEnabled = 2)")
        self.chk_auto_answer.setChecked(self.cfg.get("auto_answer", True))
        form_layout.addWidget(self.chk_auto_answer, 5, 0, 1, 2)

        # Auto Connect (Cero Intervención)
        self.chk_auto_connect = QCheckBox("Conexión Automática: Activar manos libres de inmediato al registrarse el Cisco (Cero Intervención)")
        self.chk_auto_connect.setChecked(self.cfg.get("auto_connect_on_register", True))
        form_layout.addWidget(self.chk_auto_connect, 6, 0, 1, 2)

        layout.addWidget(form_box)

        # Botones de Acción
        btn_row = QHBoxLayout()
        btn_preview = QPushButton("📄 Previsualizar SEP<MAC>.cnf.xml")
        btn_preview.clicked.connect(self._preview_xml)
        btn_save = QPushButton("💾 Guardar Configuración")
        btn_save.setObjectName("btnPrimary")
        btn_save.clicked.connect(self._save_settings)

        btn_row.addWidget(btn_preview)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

        layout.addStretch()

    def _setup_logs_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.txt_logs = QTextEdit()
        self.txt_logs.setReadOnly(True)
        self.txt_logs.setObjectName("logConsole")
        layout.addWidget(self.txt_logs)

        btn_row = QHBoxLayout()
        btn_clear = QPushButton("🗑️ Limpiar Consola")
        btn_clear.clicked.connect(self.txt_logs.clear)
        btn_copy = QPushButton("📋 Copiar Logs")
        btn_copy.clicked.connect(self._copy_logs)

        btn_row.addStretch()
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_copy)
        layout.addLayout(btn_row)

    # ==========================
    # LÓGICA DE SERVICIOS
    # ==========================

    def _start_services(self):
        local_ip = self.combo_ip.currentText()
        if not local_ip:
            local_ip = "127.0.0.1"

        sip_port = int(self.cfg.get("sip_port", 5060))
        tftp_port = int(self.cfg.get("tftp_port", 69))
        rtp_port = int(self.cfg.get("rtp_port_local", 16384))

        # 1. Iniciar TFTP Server
        try:
            self.tftp_server = TFTPServer(
                bind_ip=local_ip,
                port=tftp_port,
                tftp_root=Path("tftp_root"),
                on_log=self._post_log,
                on_phone_detected=lambda mac, ip: self.bridge_signals.phone_detected_signal.emit(mac, ip),
                get_config_func=lambda: self.cfg
            )
            self.tftp_server.start()
            self.chip_tftp.setText(f"📁 TFTP: Activo ({tftp_port})")
            self.chip_tftp.setObjectName("chipSuccess")
        except Exception as e:
            self._post_log(f"Error iniciando TFTP: {e}")
            self.chip_tftp.setText("📁 TFTP: Error")
            self.chip_tftp.setObjectName("chipDanger")

        # 2. Iniciar SIP Server
        try:
            self.sip_server = SIPServer(
                bind_ip=local_ip,
                sip_port=sip_port,
                on_log=self._post_log,
                on_phone_registered=lambda p: self.bridge_signals.phone_registered_signal.emit(p),
                on_call_state_changed=lambda s, info: self.bridge_signals.call_state_signal.emit(s, info)
            )
            self.sip_server.start(local_rtp_port=rtp_port)
            self.chip_sip.setText(f"☎ SIP: Activo ({sip_port})")
            self.chip_sip.setObjectName("chipSuccess")
        except Exception as e:
            self._post_log(f"Error iniciando SIP: {e}")
            self.chip_sip.setText("☎ SIP: Error")
            self.chip_sip.setObjectName("chipDanger")

        # 3. Inicializar RTP Engine
        self.rtp_engine = RTPAudioEngine(
            bind_ip=local_ip,
            local_port=rtp_port,
            codec=self.cfg.get("codec", "PCMU"),
            on_log=self._post_log,
            on_audio_received=self.audio_bridge.handle_incoming_cisco_pcm
        )
        self.audio_bridge.set_rtp_sender(self.rtp_engine.send_pcm_frame)

        self._refresh_chip_styles()

    def _restart_services(self):
        if self.sip_server:
            self.sip_server.stop()
        if self.tftp_server:
            self.tftp_server.stop()
        if self.rtp_engine:
            self.rtp_engine.stop()
        self.audio_bridge.stop_bridge()

        time.sleep(0.3)
        self._start_services()

    # ==========================
    # SEÑALES Y CALLBACKS
    # ==========================

    def _connect_signals(self):
        self.bridge_signals.log_signal.connect(self._handle_log)
        self.bridge_signals.phone_registered_signal.connect(self._handle_phone_registered)
        self.bridge_signals.phone_detected_signal.connect(self._handle_phone_detected)
        self.bridge_signals.call_state_signal.connect(self._handle_call_state)
        self.bridge_signals.levels_signal.connect(self._handle_levels)

    def _post_log(self, msg: str):
        self.bridge_signals.log_signal.emit(msg)

    def _post_levels(self, mic: float, spk: float):
        self.bridge_signals.levels_signal.emit(mic, spk)

    def _handle_log(self, msg: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.txt_logs.append(f"[{timestamp}] {msg}")

    def _handle_phone_detected(self, mac: str, ip: str):
        clean_mac = normalize_mac(mac)
        self._post_log(f"📱 Cisco detectado por TFTP: MAC {clean_mac} desde {ip}")
        if not self.txt_mac.text() or self.txt_mac.text() == "AC44F21AACDF":
            self.txt_mac.setText(clean_mac)
            self.cfg["cisco_mac"] = clean_mac

    def _handle_phone_registered(self, phone: dict):
        ext = phone.get("extension", "8831")
        ip = phone.get("ip", "")
        self.chip_phone.setText(f"📱 Cisco 8831: En Línea ({ip})")
        self.chip_phone.setObjectName("chipSuccess")
        self._refresh_chip_styles()
        self._post_log(f"✅ Cisco CP-8831 registrado exitosamente como extensión {ext} ({ip})")

        # Modo Cero Intervención: Conectar manos libres automáticamente al registrarse
        if self.cfg.get("auto_connect_on_register", True):
            if self.sip_server and not self.sip_server.is_in_call:
                self._post_log("🚀 Modo Cero Intervención: Conectando manos libres automáticamente...")
                QTimer.singleShot(1000, self._auto_connect_handsfree)

    def _auto_connect_handsfree(self):
        if self.sip_server and not self.sip_server.is_in_call:
            ext = self.txt_ext.text().strip() or "8831"
            try:
                self.sip_server.start_handsfree_call(extension=ext)
            except Exception as e:
                self._post_log(f"Aviso auto-conexión: {e}")

    def _handle_call_state(self, state: str, info: dict):
        if state == "connected":
            self.btn_call.setEnabled(True)
            self.btn_call.setText("🔴 FINALIZAR MANOS LIBRES (COLGAR)")
            self.btn_call.setObjectName("btnCallHangup")
            self.chip_call.setText("🟢 Manos Libres Activo")
            self.chip_call.setObjectName("chipSuccess")

            # Arrancar RTP y Audio
            remote_ip = info.get("remote_ip", "")
            remote_port = int(info.get("remote_rtp_port", 16384))
            codec_pt = int(info.get("codec_pt", 0))

            self.rtp_engine.start()
            self.rtp_engine.set_remote_endpoint(remote_ip, remote_port, codec_pt)

            in_id = self.combo_audio_in.currentData()
            out_id = self.combo_audio_out.currentData()
            self.audio_bridge.start_bridge(in_id, out_id)

            self.call_start_time = time.time()
            self.call_timer.start()

        elif state == "disconnected":
            self.btn_call.setText("📞 CONECTAR ALTAVOZ (INICIAR MANOS LIBRES)")
            self.btn_call.setObjectName("btnCallStart")
            self.chip_call.setText("⚪ Estado: En espera")
            self.chip_call.setObjectName("chipNeutral")

            self.call_timer.stop()
            self.call_start_time = None
            self.lbl_call_timer.setText("⏱️ Duración: 00:00:00")

            if self.rtp_engine:
                self.rtp_engine.stop()
            self.audio_bridge.stop_bridge()

        self._refresh_chip_styles()
        self._refresh_call_button_style()

    def _handle_levels(self, mic: float, spk: float):
        self.vu_mic.set_level(mic)
        self.vu_speaker.set_level(spk)

    def _update_call_timer(self):
        if self.call_start_time:
            elapsed = int(time.time() - self.call_start_time)
            mins, secs = divmod(elapsed, 60)
            hrs, mins = divmod(mins, 60)
            self.lbl_call_timer.setText(f"⏱️ Duración: {hrs:02d}:{mins:02d}:{secs:02d}")

    # ==========================
    # ACCIONES DEL USUARIO
    # ==========================

    def _toggle_handsfree_call(self):
        if not self.sip_server:
            return

        if not self.sip_server.is_in_call:
            ext = self.txt_ext.text().strip() or "8831"
            try:
                self.btn_call.setText("⌛ CONECTANDO ALTAVOZ...")
                self.btn_call.setEnabled(False)
                self._refresh_call_button_style()
                self.sip_server.start_handsfree_call(extension=ext)
                QTimer.singleShot(6000, self._check_call_timeout)
            except Exception as e:
                self.btn_call.setText("📞 CONECTAR ALTAVOZ (INICIAR MANOS LIBRES)")
                self.btn_call.setEnabled(True)
                self._refresh_call_button_style()
                QMessageBox.warning(self, "Aviso", str(e))
        else:
            self.sip_server.hangup_call()

    def _check_call_timeout(self):
        if self.sip_server and not self.sip_server.is_in_call:
            if not self.btn_call.isEnabled() or "CONECTANDO" in self.btn_call.text():
                self.btn_call.setText("📞 CONECTAR ALTAVOZ (INICIAR MANOS LIBRES)")
                self.btn_call.setEnabled(True)
                self._refresh_call_button_style()

    def _on_mute_clicked(self, checked: bool):
        self.audio_bridge.is_muted = checked
        if checked:
            self.btn_mute.setText("🔇 Silenciado")
            self.btn_mute.setObjectName("btnMuteActive")
        else:
            self.btn_mute.setText("🎙️ Silenciar Micrófono")
            self.btn_mute.setObjectName("btnMute")
        self._refresh_mute_button_style()

    def _on_speaker_volume_changed(self, val: int):
        self.lbl_spk_vol_val.setText(f"{val}%")
        self.audio_bridge.speaker_gain = val / 100.0

    def _on_mic_gain_changed(self, val: int):
        self.lbl_mic_gain_val.setText(f"{val}%")
        self.audio_bridge.mic_gain = val / 100.0

    def _populate_ips(self):
        self.combo_ip.blockSignals(True)
        self.combo_ip.clear()
        ips = get_local_ips()
        for ip in ips:
            self.combo_ip.addItem(ip)
        saved_ip = self.cfg.get("local_ip", "")
        if saved_ip in ips:
            self.combo_ip.setCurrentText(saved_ip)
        self.combo_ip.blockSignals(False)

    def _on_ip_changed(self, ip_str: str):
        if ip_str and ip_str != self.cfg.get("local_ip"):
            self.cfg["local_ip"] = ip_str
            save_config(self.cfg)
            self._post_log(f"IP cambiada a {ip_str}. Reiniciando servicios...")
            self._restart_services()

    def _populate_audio_devices(self):
        devs = AudioBridge.get_audio_devices()

        self.combo_audio_in.clear()
        self.combo_audio_out.clear()

        cable_in_idx = -1
        cable_out_idx = -1

        for i, d in enumerate(devs["inputs"]):
            self.combo_audio_in.addItem(d["name"], d["id"])
            raw = d.get("raw_name", "").upper()
            # CABLE Output es el audio que Teams envía hacia el altavoz del Cisco
            if "CABLE OUTPUT" in raw or "CABLE" in raw:
                cable_in_idx = i

        for i, d in enumerate(devs["outputs"]):
            self.combo_audio_out.addItem(d["name"], d["id"])
            raw = d.get("raw_name", "").upper()
            # CABLE Input es el micrófono virtual donde entra el audio del Cisco
            if "CABLE INPUT" in raw or "CABLE" in raw:
                cable_out_idx = i

        # Si se detecta VB-Audio Cable, autoseleccionarlo sin intervención del usuario
        if cable_in_idx >= 0:
            self.combo_audio_in.setCurrentIndex(cable_in_idx)
            self._post_log("🎧 Autoseleccionado CABLE Output para Altavoz Cisco")
        if cable_out_idx >= 0:
            self.combo_audio_out.setCurrentIndex(cable_out_idx)
            self._post_log("🎙️ Autoseleccionado CABLE Input para Micrófono Cisco")

        self._post_log(f"Dispositivos de sonido detectados: {len(devs['inputs'])} entradas, {len(devs['outputs'])} salidas")

    def _preview_xml(self):
        mac = self.txt_mac.text().strip() or "AC44F21AACDF"
        xml_content = generate_cisco_8831_xml(
            mac=mac,
            pc_ip=self.combo_ip.currentText(),
            sip_port=int(self.txt_sip_port.text().strip() or 5060),
            extension=self.txt_ext.text().strip() or "8831",
            display_name=self.txt_display.text().strip() or "PC Handsfree",
            auto_answer=self.chk_auto_answer.isChecked(),
            transport=self.combo_transport.currentData()
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(f"SEP{normalize_mac(mac)}.cnf.xml")
        dlg.resize(650, 500)
        d_layout = QVBoxLayout(dlg)

        txt = QTextEdit()
        txt.setPlainText(xml_content)
        txt.setReadOnly(True)
        d_layout.addWidget(txt)

        btn_box = QHBoxLayout()
        btn_copy = QPushButton("📋 Copiar XML")
        btn_copy.clicked.connect(lambda: txt.selectAll() or txt.copy())
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_copy)
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        d_layout.addLayout(btn_box)

        dlg.exec()

    def _save_settings(self):
        self.cfg["cisco_mac"] = self.txt_mac.text().strip()
        self.cfg["extension"] = self.txt_ext.text().strip()
        self.cfg["display_name"] = self.txt_display.text().strip()
        self.cfg["sip_port"] = int(self.txt_sip_port.text().strip() or 5060)
        self.cfg["transport"] = self.combo_transport.currentData()
        self.cfg["auto_answer"] = self.chk_auto_answer.isChecked()
        self.cfg["auto_connect_on_register"] = self.chk_auto_connect.isChecked()
        self.cfg["local_ip"] = self.combo_ip.currentText()

        save_config(self.cfg)
        QMessageBox.information(self, "Configuración", "Configuración guardada exitosamente.")
        self._post_log("Configuración guardada por el usuario")

    def _copy_logs(self):
        self.txt_logs.selectAll()
        self.txt_logs.copy()
        QMessageBox.information(self, "Logs", "Logs copiados al portapapeles.")

    def closeEvent(self, event):
        if self.sip_server:
            self.sip_server.stop()
        if self.tftp_server:
            self.tftp_server.stop()
        if self.rtp_engine:
            self.rtp_engine.stop()
        self.audio_bridge.stop_bridge()
        event.accept()

    # ==========================
    # ESTILOS Y TEMAS
    # ==========================

    def _refresh_chip_styles(self):
        for chip in (self.chip_tftp, self.chip_sip, self.chip_phone, self.chip_call):
            chip.style().unpolish(chip)
            chip.style().polish(chip)

    def _refresh_call_button_style(self):
        self.btn_call.style().unpolish(self.btn_call)
        self.btn_call.style().polish(self.btn_call)

    def _refresh_mute_button_style(self):
        self.btn_mute.style().unpolish(self.btn_mute)
        self.btn_mute.style().polish(self.btn_mute)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #12141A;
            }
            QWidget {
                color: #E2E8F0;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
            }
            #headerFrame {
                background-color: #1A1D26;
                border: 1px solid #2A2F3D;
                border-radius: 8px;
            }
            #appTitle {
                font-size: 18px;
                font-weight: bold;
                color: #FFFFFF;
            }
            #appSubtitle {
                font-size: 12px;
                color: #94A3B8;
            }
            #boldLabel {
                font-weight: 600;
                color: #F1F5F9;
            }
            QGroupBox {
                border: 1px solid #2A2F3D;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 16px;
                font-weight: bold;
                color: #60A5FA;
                background-color: #1A1D26;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            #tipFrame {
                background-color: #142132;
                border: 1px solid #1E3A5F;
                border-radius: 6px;
            }
            QTabWidget::pane {
                border: 1px solid #2A2F3D;
                background-color: #161922;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #1A1D26;
                color: #94A3B8;
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            #chipSuccess {
                background-color: #064E3B;
                color: #A7F3D0;
                padding: 5px 10px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 11px;
            }
            #chipWarning {
                background-color: #78350F;
                color: #FDE68A;
                padding: 5px 10px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 11px;
            }
            #chipDanger {
                background-color: #7F1D1D;
                color: #FECACA;
                padding: 5px 10px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 11px;
            }
            #chipNeutral {
                background-color: #27272A;
                color: #A1A1AA;
                padding: 5px 10px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 11px;
            }
            #btnCallStart {
                background-color: #10B981;
                color: #FFFFFF;
                font-size: 15px;
                font-weight: bold;
                border-radius: 8px;
                border: none;
            }
            #btnCallStart:hover {
                background-color: #059669;
            }
            #btnCallHangup {
                background-color: #EF4444;
                color: #FFFFFF;
                font-size: 15px;
                font-weight: bold;
                border-radius: 8px;
                border: none;
            }
            #btnCallHangup:hover {
                background-color: #DC2626;
            }
            #btnMute {
                background-color: #374151;
                color: #F3F4F6;
                padding: 6px 12px;
                border-radius: 6px;
                font-weight: 600;
                border: 1px solid #4B5563;
            }
            #btnMuteActive {
                background-color: #B91C1C;
                color: #FFFFFF;
                padding: 6px 12px;
                border-radius: 6px;
                font-weight: 600;
                border: 1px solid #DC2626;
            }
            #btnPrimary {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: bold;
                padding: 7px 16px;
                border-radius: 6px;
                border: none;
            }
            #btnPrimary:hover {
                background-color: #1D4ED8;
            }
            QPushButton {
                background-color: #262B38;
                color: #E2E8F0;
                border: 1px solid #3B4254;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #323849;
            }
            QLineEdit, QComboBox {
                background-color: #0F1117;
                color: #F8FAFC;
                border: 1px solid #2D3345;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QComboBox::drop-down {
                border: none;
            }
            #logConsole {
                background-color: #0B0D13;
                color: #38BDF8;
                border: 1px solid #1E2333;
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #27272A;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #3B82F6;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
        """)

