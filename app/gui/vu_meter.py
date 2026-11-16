"""Widget PyQt6 de Vúmetro (LED VU Meter) para monitorización en tiempo real."""

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QBrush, QPen, QFont
from PyQt6.QtWidgets import QWidget

class VUMeterWidget(QWidget):
    def __init__(self, title: str = "Nivel", parent=None):
        super().__init__(parent)
        self.title = title
        self._level = 0.0  # 0.0 a 1.0
        self._peak = 0.0
        self.setMinimumHeight(24)
        self.setMinimumWidth(120)

    def set_level(self, level: float):
        self._level = max(0.0, min(1.0, level))
        if self._level > self._peak:
            self._peak = self._level
        else:
            self._peak = max(0.0, self._peak * 0.96)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()

        # Fondo oscuro del vúmetro
        bg_rect = QRectF(0, 0, width, height)
        painter.fillRect(bg_rect, QColor(25, 27, 34))

        # Marco sutil
        painter.setPen(QPen(QColor(45, 48, 60), 1))
        painter.drawRoundedRect(bg_rect, 4, 4)

        bar_width = (width - 4) * self._level
        if bar_width > 0:
            # Gradiente de color: Verde -> Amarillo -> Rojo
            grad = QLinearGradient(2, 0, width - 4, 0)
            grad.setColorAt(0.0, QColor(46, 204, 113))   # Verde suave
            grad.setColorAt(0.65, QColor(241, 196, 15))  # Amarillo
            grad.setColorAt(0.85, QColor(231, 76, 60))   # Rojo pico

            bar_rect = QRectF(2, 2, bar_width, height - 4)
            painter.fillRect(bar_rect, QBrush(grad))

        # Línea de pico (Peak hold)
        if self._peak > 0.02:
            peak_x = 2 + (width - 4) * self._peak
            painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
            painter.drawLine(int(peak_x), 2, int(peak_x), height - 2)

        # Texto del título y porcentaje
        painter.setPen(QPen(QColor(220, 220, 225)))
        font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        painter.setFont(font)
        text = f"{self.title}: {int(self._level * 100)}%"
        painter.drawText(QRectF(8, 0, width - 16, height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

