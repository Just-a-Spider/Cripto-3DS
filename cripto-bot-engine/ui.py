import sys
import os
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QColor

class CustomWebEnginePage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        print(f"JS: {message}", flush=True)
        if message.startswith("SAVE_PIN:"):
            pin = message.split("SAVE_PIN:")[1].strip()
            with open(os.path.join(os.path.dirname(__file__), "client_pin.txt"), "w") as f:
                f.write(pin)
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)

def run_gui():
    app = QApplication(sys.argv)
    app.setStyleSheet("QMainWindow { background-color: #0f172a; }")

    window = QMainWindow()
    window.setWindowTitle('Cripto-3DS Bot Engine')
    window.resize(1000, 800)
    
    view = QWebEngineView()
    # Force the WebEngine page background to dark slate
    view.page().setBackgroundColor(QColor("#0f172a"))
    
    profile = view.page().profile()
    storage_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'web_data'))
    profile.setPersistentStoragePath(storage_path)
    
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    
    html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'dashboard.html'))
    
    pin = "1234"
    pin_file = os.path.join(os.path.dirname(__file__), "client_pin.txt")
    if os.path.exists(pin_file):
        with open(pin_file, "r") as f:
            pin = f.read().strip()
            
    url = QUrl.fromLocalFile(html_path)
    url.setFragment(f"pin={pin}")
    
    page = CustomWebEnginePage(profile, view)
    view.setPage(page)
    view.load(url)
    
    window.setCentralWidget(view)
    window.show()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    run_gui()
