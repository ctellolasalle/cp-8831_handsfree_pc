# Cisco CP-8831 Handsfree Audio Station para PC

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)]()

Aplicación de escritorio **Standalone** desarrollada en **Python** con **PyQt6** que transforma una estación de conferencia para salas **Cisco Unified IP Conference Phone 8831 (CP-8831)** en un sistema de altavoz y micrófono manos libres de alta fidelidad para PC.

Aprovecha el procesador de señal digital (**DSP**) integrado en el hardware del Cisco para **cancelación acústica de eco (AEC)** en sala y su matriz de micrófonos omnidireccionales en 360°, ofreciendo una calidad de sonido corporativa muy superior a la de los altavoces y micrófonos de escritorio convencionales.

---

## 🏗️ Arquitectura del Sistema

```
┌────────────────────────────────────────────────────────────────────────┐
│             APLICACIÓN ESCRITORIO STANDALONE (PyQt6)                   │
│                                                                        │
│  ┌───────────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │ Servidor TFTP Embebido│  │ Servidor SIP/UAS│  │ Motor RTP & Audio│  │
│  │ RFC 1350 / 2348       │  │ Dual TCP / UDP  │  │ (sounddevice +   │  │
│  │ (Provisión SEP<MAC>)  │  │ Puerto 5060     │  │  audioop G.711)  │  │
│  └───────────┬───────────┘  └────────┬────────┘  └────────┬─────────┘  │
└──────────────┼───────────────────────┼────────────────────┼────────────┘
               │                       │                    │
               │ SEP<MAC>.cnf.xml      │ SIP Señalización   │ Audio RTP (20ms)
               ▼                       ▼                    ▼
     ┌──────────────────────────────────────────────────────────┐
     │            Teléfono Cisco CP-8831 (LAN / Ethernet)       │
     │            (Matriz Micrófonos + Altavoz + DSP Eco)       │
     └──────────────────────────────────────────────────────────┘
```

---

## 🌟 Características Principales

* **Servidor TFTP RFC 1350 / RFC 2348 Embebido (UDP 69):**
  * Genera al vuelo el archivo de configuración `SEP<MAC>.cnf.xml` adaptado específicamente para el CP-8831.
  * Sirve el plan de marcado `dialplan.xml` y los paquetes de idioma y tonos oficiales de Cisco (`sl-sip.jar`, `g3-tones.xml`).
  * Autodetección de la dirección MAC del teléfono en cuanto se conecta a la red.

* **Servidor SIP / Agente de Llamadas Dual (TCP y UDP en Puerto 5060):**
  * Soporte simultáneo para **TCP** (recomendado por Cisco para terminales serie 8800) y **UDP**.
  * Acepta registros automáticos de la extensión (`8831`) y mantiene el estado en línea del equipo.
  * Control total de llamada: **Auto-Answer en Manos Libres** (`autoAnswerEnabled = 2`). Al pulsar *"Conectar Altavoz"* en la PC, el teléfono abre de inmediato el altavoz y los micrófonos sin timbrar ni requerir intervención física.

* **Motor RTP de Baja Latencia y Puente de Audio Directo:**
  * Flujo de paquetes RTP RFC 3550 en tramas continuas de 20 ms con códec **G.711u (PCMU)** o **G.711a (PCMA)**.
  * Enrutamiento con cualquier tarjeta de sonido o controlador de audio virtual mediante `sounddevice` (WASAPI / DirectSound).
  * Vúmetros animados LED (Verde / Amarillo / Rojo con retención de picos) para entrada y salida.
  * Deslizadores de ganancia/volumen independientes y botón de silenciado rápido (**Mute**).

---

## 📋 Requisitos e Instalación

### Opción A: Ejecutable Standalone (.exe)
Si descargaste o compilaste el ejecutable standalone:
1. Simplemente ejecuta `dist/Cisco_CP8831_Handsfree.exe`. No requiere tener Python instalado.

### Opción B: Ejecutar desde Código Fuente
1. Clona el repositorio:
   ```bash
   git clone https://github.com/ctellolasalle/cp-8831_handsfree_pc.git
   cd cp-8831_handsfree_pc
   ```
2. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Inicia la aplicación:
   ```bash
   python main.py
   ```

---

## 📱 Configuración del Cisco CP-8831

1. **Conexión física:** Conecta el puerto de red (LAN) del Cisco CP-8831 al mismo switch o router que tu PC (o directamente al puerto Ethernet de tu ordenador).
2. **Asignación del Servidor TFTP:**
   * **Vía DHCP (Recomendado):** En tu router o servidor DHCP local, configura la **Opción 150** o **Opción 66** apuntando a la IP local de tu PC (ej. `10.0.10.102`).
   * **Vía Menú en Pantalla del Cisco:**
     1. Presiona el botón de **Configuración / Settings** en el teléfono.
     2. Navega a **Admin Settings** -> **Network Configuration** -> **IPv4 Configuration**.
     3. Desbloquea la edición presionando la combinación de teclas `**#`.
     4. En el campo **TFTP Server 1**, escribe la dirección IP de tu PC.
     5. Guarda los cambios y reinicia el teléfono.
3. Al encenderse, el teléfono solicitará su archivo `SEP<MAC>.cnf.xml`, se registrará en la aplicación y el indicador mostrará: **"📱 Cisco 8831: En Línea"**.

---

## 🎧 Enrutamiento con Microsoft Teams, Zoom, Google Meet o Discord

Para que tus programas de videollamada utilicen el altavoz y los micrófonos del Cisco:

1. Instala un controlador de cable de audio virtual gratuito como **[VB-Audio Virtual Cable](https://vb-audio.com/Cable/)**.
2. **En esta aplicación (Cisco Handsfree Station):**
   * **Sonido hacia Altavoz Cisco:** Selecciona `CABLE Output` (lo que reproduce la PC).
   * **Micrófono Cisco hacia PC:** Selecciona `CABLE Input` (lo que escucha la PC).
3. **En Teams, Zoom, Google Meet o Discord:**
   * **Altavoz:** Selecciona `CABLE Input` (el sonido de los demás participantes sale por el altavoz del Cisco).
   * **Micrófono:** Selecciona `CABLE Output` (el audio captado por los micrófonos del Cisco entra a tu llamada).

> [!TIP]
> El hardware DSP del CP-8831 cancela automáticamente en tiempo real el audio que sale por su altavoz, garantizando una llamada libre de ecos y acoples sin saturar la CPU de tu ordenador.

---

## 🔨 Compilación a Ejecutable Standalone (.exe) con PyInstaller

Para compilar el proyecto en un único archivo ejecutable `.exe` independiente:

```powershell
pyinstaller --noconfirm --onedir --windowed `
  --name "Cisco_CP8831_Handsfree" `
  --add-data "tftp_root;tftp_root" `
  main.py
```

El ejecutable resultante se ubicará en la carpeta `dist/Cisco_CP8831_Handsfree/Cisco_CP8831_Handsfree.exe`.

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo [LICENSE](LICENSE) para más detalles.
