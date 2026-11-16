"""Punto de entrada de la aplicación Cisco CP-8831 Handsfree Audio Station."""

import sys
import os

# Asegurar que el directorio raíz del proyecto esté en el path de Python
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from app.gui.main_window import MainWindow

def main():
    # Habilitar soporte High DPI para pantallas modernas
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Cisco CP-8831 Handsfree Station")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()

