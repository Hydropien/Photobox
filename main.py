#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import uuid
import shutil
from dataclasses import dataclass
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

# Webcam optional (Preview + Capture)
try:
    import cv2
    CV_AVAILABLE = True
except Exception:
    CV_AVAILABLE = False


@dataclass(frozen=True)
class CaptureResult:
    token: str
    tmp_path: str   # temporäres Foto (noch nicht final gespeichert)


class WebcamPreview(QtCore.QObject):
    """Live-Vorschau via OpenCV. Läuft im GUI-Thread über QTimer."""
    frame_ready = QtCore.pyqtSignal(QtGui.QImage)
    status = QtCore.pyqtSignal(str)

    def __init__(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        super().__init__()
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps

        self._cap = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not CV_AVAILABLE:
            self.status.emit("Webcam: OpenCV nicht installiert.")
            return

        self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self._cap = None
            self.status.emit("Webcam: konnte nicht geöffnet werden (device_index prüfen).")
            return

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))

        interval_ms = max(15, int(1000 / max(1, self.fps)))
        self._timer.start(interval_ms)
        self.status.emit("Webcam: Vorschau aktiv.")

    def stop(self) -> None:
        self._timer.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None
        self.status.emit("Webcam: gestoppt.")

    def _tick(self) -> None:
        if self._cap is None:
            return
        ok, frame_bgr = self._cap.read()
        if not ok or frame_bgr is None:
            self.status.emit("Webcam: Frame konnte nicht gelesen werden.")
            return

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qimg = QtGui.QImage(frame_rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888)
        self.frame_ready.emit(qimg.copy())


class CaptureWorker(QtCore.QObject):
    """Nimmt ein Foto auf (Webcam) und speichert es temporär."""
    finished = QtCore.pyqtSignal(object)     # CaptureResult
    error = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(str)

    def __init__(self, tmp_dir: str, device_index: int = 0, use_webcam: bool = True):
        super().__init__()
        self._tmp_dir = tmp_dir
        self._device_index = device_index
        self._use_webcam = use_webcam

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            token = uuid.uuid4().hex
            os.makedirs(self._tmp_dir, exist_ok=True)
            tmp_path = os.path.join(self._tmp_dir, f"{token}.jpg")

            self.progress.emit("Foto wird aufgenommen …")

            ok = False
            if self._use_webcam and CV_AVAILABLE:
                ok = self._capture_from_webcam(tmp_path)

            if not ok:
                self._write_dummy_image(tmp_path, token)

            self.finished.emit(CaptureResult(token=token, tmp_path=tmp_path))
        except Exception as e:
            self.error.emit(f"CaptureWorker-Fehler: {type(e).__name__}: {e}")

    def _capture_from_webcam(self, out_path: str) -> bool:
        try:
            cap = cv2.VideoCapture(self._device_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                return False

            for _ in range(5):  # warmup
                cap.read()

            ok, frame = cap.read()
            cap.release()

            if not ok or frame is None:
                return False

            params = [int(cv2.IMWRITE_JPEG_QUALITY), 92]
            return bool(cv2.imwrite(out_path, frame, params))
        except Exception:
            return False

    def _write_dummy_image(self, out_path: str, token: str) -> None:
        img = QtGui.QImage(1600, 1000, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor("black"))
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor("white"), 8))
        p.setFont(QtGui.QFont("Sans", 64, QtGui.QFont.Bold))
        p.drawText(img.rect(), QtCore.Qt.AlignCenter, "PHOTOBOX DEMO")
        p.setFont(QtGui.QFont("Sans", 26))
        p.drawText(40, 80, time.strftime("%Y-%m-%d %H:%M:%S"))
        p.drawText(40, 130, f"token: {token[:12]}…")
        p.end()
        img.save(out_path, "JPG", quality=92)


class FlashOverlay(QtWidgets.QWidget):
    """Weißes Vollbild-Overlay als Blitz."""
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background-color: white;")
        self.hide()

    def flash(self, ms: int = 120) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        QtCore.QTimer.singleShot(ms, self.hide)


class PhotoboxWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # --- Konfiguration (Demo) ---
        self.device_index = 0
        base = os.path.dirname(__file__)
        self.keep_dir = os.path.join(base, "data_keep")
        self.tmp_dir = os.path.join(base, "data_tmp")

        os.makedirs(self.keep_dir, exist_ok=True)
        os.makedirs(self.tmp_dir, exist_ok=True)

        self.setWindowTitle("Photobox – Review Demo (Webcam)")
        self.setStyleSheet("background-color: #0b0b0b; color: white;")
        self.setCursor(QtCore.Qt.BlankCursor)

        self._locked = False
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[CaptureWorker] = None

        self._last_capture: Optional[CaptureResult] = None

        # --- UI: Stacked States ---
        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)

        self.page_idle = self._build_idle_page()
        self.page_countdown = self._build_countdown_page()
        self.page_review = self._build_review_page()

        self.stack.addWidget(self.page_idle)
        self.stack.addWidget(self.page_countdown)
        self.stack.addWidget(self.page_review)
        self.stack.setCurrentWidget(self.page_idle)

        # Blitz-Overlay
        self.flash_overlay = FlashOverlay(self)

        # Countdown Timer
        self.countdown_value = 3
        self.countdown_timer = QtCore.QTimer(self)
        self.countdown_timer.setInterval(900)
        self.countdown_timer.timeout.connect(self._countdown_tick)

        # Webcam preview
        self.webcam = WebcamPreview(device_index=self.device_index, width=1280, height=720, fps=30)
        self.webcam.frame_ready.connect(self._on_preview_frame)
        self.webcam.status.connect(self._set_status)
        self.webcam.start()

        self.showFullScreen()

    # ---------- UI ----------

    def _build_idle_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("FOTOBOX")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setFont(QtGui.QFont("Sans", 56, QtGui.QFont.Black))

        self.lbl_live = QtWidgets.QLabel()
        self.lbl_live.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_live.setMinimumSize(900, 520)
        self.lbl_live.setStyleSheet("background-color: #111; border-radius: 18px;")

        hint = QtWidgets.QLabel("Start drücken oder SPACE. (ESC beendet)")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        hint.setFont(QtGui.QFont("Sans", 18))
        hint.setStyleSheet("color: #d0d0d0;")

        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.setMinimumHeight(80)
        self.btn_start.setFont(QtGui.QFont("Sans", 24, QtGui.QFont.Bold))
        self.btn_start.setStyleSheet("""
            QPushButton { background-color: #1f6feb; border: none; border-radius: 16px; }
            QPushButton:hover { background-color: #2b7fff; }
            QPushButton:pressed { background-color: #195bbf; }
        """)
        self.btn_start.clicked.connect(self.start_flow)

        self.lbl_status_global = QtWidgets.QLabel("Status: —")
        self.lbl_status_global.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_status_global.setWordWrap(True)
        self.lbl_status_global.setFont(QtGui.QFont("Sans", 14))
        self.lbl_status_global.setStyleSheet("color: #bdbdbd;")

        layout.addWidget(title)
        layout.addWidget(self.lbl_live, 1)
        layout.addWidget(hint)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.lbl_status_global)
        return w

    def _build_countdown_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.setSpacing(14)

        self.lbl_countdown = QtWidgets.QLabel("3")
        self.lbl_countdown.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_countdown.setFont(QtGui.QFont("Sans", 160, QtGui.QFont.Black))

        self.lbl_countdown_hint = QtWidgets.QLabel("Bereit machen …")
        self.lbl_countdown_hint.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_countdown_hint.setFont(QtGui.QFont("Sans", 26))

        self.lbl_countdown_status = QtWidgets.QLabel("Status: —")
        self.lbl_countdown_status.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_countdown_status.setFont(QtGui.QFont("Sans", 14))
        self.lbl_countdown_status.setStyleSheet("color: #bdbdbd;")
        self.lbl_countdown_status.setWordWrap(True)

        layout.addStretch(1)
        layout.addWidget(self.lbl_countdown)
        layout.addWidget(self.lbl_countdown_hint)
        layout.addSpacing(6)
        layout.addWidget(self.lbl_countdown_status)
        layout.addStretch(1)
        return w

    def _build_review_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(18)

        title = QtWidgets.QLabel("Review")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setFont(QtGui.QFont("Sans", 40, QtGui.QFont.Black))

        self.lbl_review_photo = QtWidgets.QLabel()
        self.lbl_review_photo.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_review_photo.setMinimumSize(900, 560)
        self.lbl_review_photo.setStyleSheet("background-color: #111; border-radius: 18px;")

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(18)

        self.btn_keep = QtWidgets.QPushButton("KEEP")
        self.btn_keep.setMinimumHeight(85)
        self.btn_keep.setFont(QtGui.QFont("Sans", 24, QtGui.QFont.Bold))
        self.btn_keep.setStyleSheet("""
            QPushButton { background-color: #2dba4e; border: none; border-radius: 16px; }
            QPushButton:hover { background-color: #36d15b; }
            QPushButton:pressed { background-color: #249640; }
        """)
        self.btn_keep.clicked.connect(self._keep_photo)

        self.btn_throw = QtWidgets.QPushButton("THROW AWAY")
        self.btn_throw.setMinimumHeight(85)
        self.btn_throw.setFont(QtGui.QFont("Sans", 24, QtGui.QFont.Bold))
        self.btn_throw.setStyleSheet("""
            QPushButton { background-color: #d73a49; border: none; border-radius: 16px; }
            QPushButton:hover { background-color: #f05263; }
            QPushButton:pressed { background-color: #b02a37; }
        """)
        self.btn_throw.clicked.connect(self._throw_photo)

        btn_row.addWidget(self.btn_throw)
        btn_row.addWidget(self.btn_keep)

        self.lbl_review_status = QtWidgets.QLabel("Status: —")
        self.lbl_review_status.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_review_status.setWordWrap(True)
        self.lbl_review_status.setFont(QtGui.QFont("Sans", 14))
        self.lbl_review_status.setStyleSheet("color: #bdbdbd;")

        layout.addWidget(title)
        layout.addWidget(self.lbl_review_photo, 1)
        layout.addLayout(btn_row)
        layout.addWidget(self.lbl_review_status)
        return w

    # ---------- Preview handling ----------

    def _on_preview_frame(self, qimg: QtGui.QImage) -> None:
        if not hasattr(self, "lbl_live"):
            return
        pix = QtGui.QPixmap.fromImage(qimg)
        self.lbl_live.setPixmap(pix.scaled(
            self.lbl_live.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        ))

    # ---------- Flow ----------

    def start_flow(self) -> None:
        if self._locked:
            return
        self._locked = True

        # Preview stoppen -> Kamera freigeben
        try:
            self.webcam.stop()
        except Exception:
            pass

        self.stack.setCurrentWidget(self.page_countdown)
        self.countdown_value = 3
        self.lbl_countdown.setText(str(self.countdown_value))
        self.lbl_countdown_hint.setText("Bereit machen …")
        self._set_status("Countdown läuft …")
        self.countdown_timer.start()

    def _countdown_tick(self) -> None:
        self.countdown_value -= 1
        if self.countdown_value > 0:
            self.lbl_countdown.setText(str(self.countdown_value))
            return

        self.countdown_timer.stop()
        self.lbl_countdown.setText("😊")
        self.lbl_countdown_hint.setText("Bitte lächeln!")

        # Blitz (weißes Overlay) kurz vor der Aufnahme
        self.flash_overlay.flash(ms=120)

        self._set_status("Aufnahme startet …")
        QtCore.QTimer.singleShot(130, self._start_capture_async)

    def _start_capture_async(self) -> None:
        self._thread = QtCore.QThread(self)
        self._worker = CaptureWorker(
            tmp_dir=self.tmp_dir,
            device_index=self.device_index,
            use_webcam=True
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._set_status)
        self._worker.finished.connect(self._capture_finished)
        self._worker.error.connect(self._capture_error)

        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _capture_finished(self, result: CaptureResult) -> None:
        self._last_capture = result

        # Review-Seite füllen
        self.stack.setCurrentWidget(self.page_review)

        pix = QtGui.QPixmap(result.tmp_path)
        if not pix.isNull():
            self.lbl_review_photo.setPixmap(pix.scaled(
                self.lbl_review_photo.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            ))
        else:
            self.lbl_review_photo.setText("Bild konnte nicht geladen werden.")

        self._set_status(f"Foto aufgenommen. Entscheide: KEEP oder THROW AWAY. (tmp: {result.tmp_path})")
        self._locked = False

    def _capture_error(self, msg: str) -> None:
        self.stack.setCurrentWidget(self.page_countdown)
        self.lbl_countdown.setText("!")
        self.lbl_countdown_hint.setText("Fehler")
        self._set_status(msg)

        # Preview wieder starten
        try:
            self.webcam.start()
        except Exception:
            pass

        self._locked = False

    # ---------- Review actions ----------

    def _keep_photo(self) -> None:
        if not self._last_capture:
            self._set_status("Kein Foto vorhanden.")
            return

        token = self._last_capture.token
        src = self._last_capture.tmp_path
        dst = os.path.join(self.keep_dir, f"{token}.jpg")

        try:
            os.makedirs(self.keep_dir, exist_ok=True)
            # move ist atomar, wenn gleiches Filesystem; sonst copy+delete.
            shutil.move(src, dst)
            self._set_status(f"KEEP: gespeichert unter {dst}")
        except Exception as e:
            self._set_status(f"KEEP-Fehler: {type(e).__name__}: {e}")
            return
        finally:
            self._last_capture = None

        self._return_to_idle()

    def _throw_photo(self) -> None:
        if not self._last_capture:
            self._set_status("Kein Foto vorhanden.")
            return

        src = self._last_capture.tmp_path
        try:
            if os.path.exists(src):
                os.remove(src)
            self._set_status("THROW AWAY: Foto verworfen.")
        except Exception as e:
            self._set_status(f"THROW-Fehler: {type(e).__name__}: {e}")
            return
        finally:
            self._last_capture = None

        self._return_to_idle()

    def _return_to_idle(self) -> None:
        # Preview wieder starten
        try:
            self.webcam.start()
        except Exception:
            pass
        self.stack.setCurrentWidget(self.page_idle)

    # ---------- Status ----------

    def _set_status(self, msg: str) -> None:
        if hasattr(self, "lbl_status_global"):
            self.lbl_status_global.setText(f"Status: {msg}")
        if hasattr(self, "lbl_countdown_status"):
            self.lbl_countdown_status.setText(f"Status: {msg}")
        if hasattr(self, "lbl_review_status"):
            self.lbl_review_status.setText(f"Status: {msg}")

    # ---------- Keys ----------

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            return
        if event.key() == QtCore.Qt.Key_Space:
            # digitaler „Button“
            if self.stack.currentWidget() == self.page_idle:
                self.start_flow()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.webcam.stop()
        except Exception:
            pass
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(800)
        except Exception:
            pass
        event.accept()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    w = PhotoboxWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
