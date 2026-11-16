"""Puente de audio entre el sistema operativo Windows (sounddevice) y el motor RTP."""

import audioop
import queue
import threading
import time
from typing import Callable, Dict, List, Optional
import sounddevice as sd

class AudioBridge:
    def __init__(self, on_log: Optional[Callable[[str], None]] = None,
                 on_levels: Optional[Callable[[float, float], None]] = None):
        self.on_log = on_log or (lambda msg: None)
        self.on_levels = on_levels or (lambda mic, spk: None)

        self.input_device_id: Optional[int] = None
        self.output_device_id: Optional[int] = None

        self.speaker_gain = 1.0  # Ganancia hacia altavoz de Cisco
        self.mic_gain = 1.0      # Ganancia desde micrófono de Cisco
        self.is_muted = False     # Silencio de micrófono hacia PC / hacia Cisco

        self._in_stream: Optional[sd.RawInputStream] = None
        self._out_stream: Optional[sd.RawOutputStream] = None
        self._running = False

        self._rtp_send_func: Optional[Callable[[bytes], None]] = None

        # Cola de reproducción hacia el dispositivo de salida de Windows (Micrófono virtual)
        self._playback_queue = queue.Queue(maxsize=100)
        self._resample_state_in = None
        self._resample_state_out = None

        # Medidores de nivel RMS
        self._last_mic_level = 0.0
        self._last_spk_level = 0.0

        # Hilo de reporte de niveles periódicos
        self._level_timer: Optional[threading.Thread] = None

    @staticmethod
    def get_audio_devices() -> Dict[str, List[Dict]]:
        """Devuelve listas de dispositivos de entrada y salida disponibles."""
        inputs = []
        outputs = []
        try:
            devs = sd.query_devices()
            for idx, d in enumerate(devs):
                name = d.get("name", f"Dispositivo {idx}")
                hostapi = sd.query_hostapis(d.get("hostapi", 0)).get("name", "")
                full_name = f"{name} ({hostapi})"
                
                if d.get("max_input_channels", 0) > 0:
                    inputs.append({
                        "id": idx,
                        "name": full_name,
                        "raw_name": name,
                        "samplerate": int(d.get("default_samplerate", 48000)),
                        "channels": d.get("max_input_channels")
                    })
                if d.get("max_output_channels", 0) > 0:
                    outputs.append({
                        "id": idx,
                        "name": full_name,
                        "raw_name": name,
                        "samplerate": int(d.get("default_samplerate", 48000)),
                        "channels": d.get("max_output_channels")
                    })
        except Exception as e:
            pass
        return {"inputs": inputs, "outputs": outputs}

    def set_rtp_sender(self, sender_func: Callable[[bytes], None]):
        self._rtp_send_func = sender_func

    def handle_incoming_cisco_pcm(self, pcm_8k: bytes):
        """
        Recibe audio del micrófono del Cisco (8000Hz 16-bit mono linear)
        y lo encola para reproducción en el dispositivo de salida de Windows.
        """
        if not self._running:
            return

        try:
            # Medir nivel del micrófono del Cisco
            rms = audioop.rms(pcm_8k, 2)
            self._last_mic_level = min(1.0, (rms / 16000.0) * self.mic_gain)

            if self.is_muted:
                # Si está en silencio, emitir ceros
                processed_8k = b"\x00" * len(pcm_8k)
            else:
                if self.mic_gain != 1.0:
                    processed_8k = audioop.mul(pcm_8k, 2, self.mic_gain)
                else:
                    processed_8k = pcm_8k

            # Resamplear si la salida no es 8000Hz
            out_rate = self.out_samplerate
            if out_rate != 8000:
                converted, self._resample_state_in = audioop.ratecv(
                    processed_8k, 2, 1, 8000, out_rate, self._resample_state_in
                )
            else:
                converted = processed_8k

            try:
                self._playback_queue.put_nowait(converted)
            except queue.Full:
                # Evitar acumular latencia descartando el buffer más viejo
                try:
                    self._playback_queue.get_nowait()
                    self._playback_queue.put_nowait(converted)
                except Exception:
                    pass
        except Exception as e:
            pass

    def start_bridge(self, input_dev_id: Optional[int], output_dev_id: Optional[int]):
        if self._running:
            self.stop_bridge()

        self._running = True
        self._resample_state_in = None
        self._resample_state_out = None

        # Obtener información de dispositivos
        try:
            in_info = sd.query_devices(input_dev_id) if input_dev_id is not None else sd.query_devices(kind="input")
            self.in_samplerate = int(in_info.get("default_samplerate", 48000))
            self.in_channels = 1
        except Exception:
            self.in_samplerate = 48000
            self.in_channels = 1

        try:
            out_info = sd.query_devices(output_dev_id) if output_dev_id is not None else sd.query_devices(kind="output")
            self.out_samplerate = int(out_info.get("default_samplerate", 48000))
            self.out_channels = 1
        except Exception:
            self.out_samplerate = 48000
            self.out_channels = 1

        # Frame de 20ms para captura
        in_blocksize = int(self.in_samplerate * 0.020)
        out_blocksize = int(self.out_samplerate * 0.020)

        # 1. Stream de Entrada (Captura audio de PC para enviar al altavoz del Cisco)
        def input_callback(indata, frames, time_info, status):
            if not self._running or not self._rtp_send_func:
                return

            pcm_raw = bytes(indata)
            # Medir nivel del audio saliente hacia el Cisco
            rms = audioop.rms(pcm_raw, 2)
            self._last_spk_level = min(1.0, (rms / 16000.0) * self.speaker_gain)

            # Aplicar ganancia de altavoz
            if self.speaker_gain != 1.0:
                pcm_amplified = audioop.mul(pcm_raw, 2, self.speaker_gain)
            else:
                pcm_amplified = pcm_raw

            # Resamplear a 8000Hz para RTP
            if self.in_samplerate != 8000:
                pcm_8k, self._resample_state_out = audioop.ratecv(
                    pcm_amplified, 2, 1, self.in_samplerate, 8000, self._resample_state_out
                )
            else:
                pcm_8k = pcm_amplified

            # Asegurar bloques de 160 muestras (320 bytes)
            # Si hay más o menos, enviar en chunks de 320
            idx = 0
            while idx + 320 <= len(pcm_8k):
                chunk = pcm_8k[idx:idx + 320]
                self._rtp_send_func(chunk)
                idx += 320

        # 2. Stream de Salida (Reproduce audio del micrófono del Cisco hacia Windows)
        def output_callback(outdata, frames, time_info, status):
            bytes_needed = frames * 2  # 16-bit mono = 2 bytes por muestra
            try:
                data = self._playback_queue.get_nowait()
                if len(data) >= bytes_needed:
                    outdata[:bytes_needed] = data[:bytes_needed]
                else:
                    outdata[:len(data)] = data
                    outdata[len(data):] = b"\x00" * (bytes_needed - len(data))
            except queue.Empty:
                outdata[:] = b"\x00" * bytes_needed

        try:
            self._in_stream = sd.RawInputStream(
                device=input_dev_id,
                channels=1,
                samplerate=self.in_samplerate,
                blocksize=in_blocksize,
                dtype="int16",
                callback=input_callback
            )
            self._out_stream = sd.RawOutputStream(
                device=output_dev_id,
                channels=1,
                samplerate=self.out_samplerate,
                blocksize=out_blocksize,
                dtype="int16",
                callback=output_callback
            )

            self._in_stream.start()
            self._out_stream.start()
            self.on_log(f"[AUDIO] Streams iniciados. IN: {self.in_samplerate}Hz | OUT: {self.out_samplerate}Hz")
        except Exception as e:
            self.on_log(f"[AUDIO] Error iniciando streams de audio: {e}")
            self.stop_bridge()
            return

        # Arrancar hilo de monitoreo de niveles para vúmetros
        self._level_timer = threading.Thread(target=self._level_monitor_loop, daemon=True)
        self._level_timer.start()

    def _level_monitor_loop(self):
        while self._running:
            self.on_levels(self._last_mic_level, self._last_spk_level)
            # Decaimiento suave del vúmetro
            self._last_mic_level = max(0.0, self._last_mic_level * 0.8)
            self._last_spk_level = max(0.0, self._last_spk_level * 0.8)
            time.sleep(0.05)

    def stop_bridge(self):
        self._running = False
        if self._in_stream:
            try:
                self._in_stream.stop()
                self._in_stream.close()
            except Exception:
                pass
            self._in_stream = None

        if self._out_stream:
            try:
                self._out_stream.stop()
                self._out_stream.close()
            except Exception:
                pass
            self._out_stream = None

        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except Exception:
                break

        self.on_levels(0.0, 0.0)
        self.on_log("[AUDIO] Streams de audio detenidos")

