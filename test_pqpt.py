from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtCore import Qt
import sys

app = QApplication(sys.argv)

window = QWidget()
window.setWindowTitle("PyQt5 Test")
window.setGeometry(100, 100, 300, 200)

label = QLabel("If you see this, PyQt5 works!", window)
label.setAlignment(Qt.AlignCenter)
label.setGeometry(0, 0, 300, 200)

window.show()

print("Window should be visible now...")
sys.exit(app.exec_())