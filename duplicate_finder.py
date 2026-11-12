# duplicate_finder.py
"""
near-duplicate image finder
"""

import sys
import os
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional, Set

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox, QSpinBox, QProgressDialog,
    QFileDialog, QListWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QGroupBox,
)
from PySide6.QtGui import (
    QPixmap, QPainter, QWheelEvent, QTransform, QShortcut, QKeySequence, QPen, QColor,
)
from PySide6.QtCore import Qt, QPoint, QThread, Signal

from PIL import Image as PILImage
import imagehash

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# In-memory image record
# ---------------------------------------------------------------------------

@dataclass
class ImageInfo:
    path: str
    filename: str
    folder: str
    width: int
    height: int
    filesize: int
    phash: str

    @property
    def id(self) -> str:
        return self.path


def hamming_distance(h1: str, h2: str) -> int:
    return (int(h1, 16) ^ int(h2, 16)).bit_count()


def iter_image_paths(roots: List[str]):
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                yield path


def build_image_info(path: Path) -> Optional[ImageInfo]:
    try:
        st = path.stat()
        with PILImage.open(path) as img:
            width, height = img.size
            ph = str(imagehash.phash(img))
        return ImageInfo(
            path=str(path),
            filename=path.name,
            folder=str(path.parent),
            width=width,
            height=height,
            filesize=st.st_size,
            phash=ph,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, roots: List[str]):
        super().__init__()
        self.roots = roots

    def run(self):
        try:
            paths = list(iter_image_paths(self.roots))
            total = len(paths)
            images: List[ImageInfo] = []
            for i, path in enumerate(paths):
                info = build_image_info(path)
                if info:
                    images.append(info)
                if i % 5 == 0 or i + 1 == total:
                    self.progress.emit(i + 1, total, str(path))
            self.finished.emit(images)
        except Exception as e:
            self.error.emit(str(e))


class MatchWorker(QThread):
    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, images: List[ImageInfo], threshold: int = 12):
        super().__init__()
        self.images = images
        self.threshold = threshold

    def run(self):
        try:
            matches = []
            n = len(self.images)
            for i in range(n):
                if i % 20 == 0:
                    self.progress.emit(i, n)
                img1 = self.images[i]
                h1 = int(img1.phash, 16)
                for j in range(i + 1, n):
                    img2 = self.images[j]
                    d = (h1 ^ int(img2.phash, 16)).bit_count()
                    if d <= self.threshold:
                        matches.append((d, img1, img2))
            matches.sort(key=lambda x: x[0])
            self.progress.emit(n, n)
            self.finished.emit(matches)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Zoomable image viewer (shared relative zoom)
# ---------------------------------------------------------------------------

class ZoomableImageViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = None
        self.base_zoom = 1.0
        self.highlight = False
        self.setMinimumSize(400, 400)
        self.setCursor(Qt.OpenHandCursor)
        self.get_relative = None
        self.set_relative = None
        self.last_pos = None

    def set_shared(self, get_func, set_func):
        self.get_relative = get_func
        self.set_relative = set_func

    def set_image(self, image_path: str):
        if not image_path or not os.path.exists(image_path):
            self.pixmap = None
            self.update()
            return
        try:
            self.pixmap = QPixmap(image_path)
            if self.pixmap.isNull():
                raise RuntimeError("null pixmap")
            try:
                with PILImage.open(image_path) as pil:
                    orientation = pil.getexif().get(274)
                    if orientation and orientation != 1:
                        t = QTransform()
                        if orientation == 3:
                            t.rotate(180)
                        elif orientation == 6:
                            t.rotate(90)
                        elif orientation == 8:
                            t.rotate(270)
                        self.pixmap = self.pixmap.transformed(t, Qt.SmoothTransformation)
            except Exception:
                pass
        except Exception as e:
            print(f"Failed to load {image_path}: {e}")
            self.pixmap = None
        self.update()

    def compute_base_zoom(self):
        if not self.pixmap or self.pixmap.isNull():
            self.base_zoom = 1.0
            return
        w, h = max(self.width(), 1), max(self.height(), 1)
        iw, ih = self.pixmap.width(), self.pixmap.height()
        self.base_zoom = min(w / iw, h / ih) * 0.92

    def fit_to_widget(self):
        self.compute_base_zoom()
        if self.set_relative:
            self.set_relative(1.0, QPoint(0, 0))
        self.update()

    def paintEvent(self, event):
        if not self.pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rel, offset = self.get_relative() if self.get_relative else (1.0, QPoint(0, 0))
        zoom = self.base_zoom * rel
        scaled = self.pixmap.scaled(
            max(1, int(self.pixmap.width() * zoom)),
            max(1, int(self.pixmap.height() * zoom)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2 + offset.x()
        y = (self.height() - scaled.height()) // 2 + offset.y()
        painter.drawPixmap(x, y, scaled)
        if self.highlight:
            painter.setPen(QPen(QColor(0, 200, 0), 6))
            painter.drawRect(self.rect().adjusted(3, 3, -3, -3))

    def wheelEvent(self, event: QWheelEvent):
        if not self.get_relative or not self.set_relative:
            return
        rel, offset = self.get_relative()
        old = rel
        rel *= 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        rel = max(0.1, min(rel, 20.0))
        mouse = event.position().toPoint()
        offset = mouse - (mouse - offset) * (rel / old)
        self.set_relative(rel, offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self.last_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.last_pos is not None and self.set_relative:
            rel, offset = self.get_relative()
            delta = event.position().toPoint() - self.last_pos
            offset += delta
            self.last_pos = event.position().toPoint()
            self.set_relative(rel, offset)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        self.last_pos = None

    def set_highlight(self, enabled: bool):
        self.highlight = enabled
        self.update()


# ---------------------------------------------------------------------------
# Comparison window
# ---------------------------------------------------------------------------

class ComparisonWindow(QMainWindow):
    def __init__(self, matches: List[Tuple], title="Duplicates", parent=None):
        super().__init__(parent)
        self.matches = matches
        self.current_idx = 0
        self.list_window = None
        self.parent_window = parent
        self.setWindowTitle(title)
        self.showMaximized()
        self.setFocusPolicy(Qt.StrongFocus)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.header = QLabel()
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setStyleSheet("font-size: 16px; font-weight: bold;")
        main_layout.addWidget(self.header)

        images_layout = QHBoxLayout()
        self.left_viewer = ZoomableImageViewer()
        self.right_viewer = ZoomableImageViewer()
        self.left_viewer.setMinimumSize(700, 700)
        self.right_viewer.setMinimumSize(700, 700)
        images_layout.addWidget(self.left_viewer)
        images_layout.addWidget(self.right_viewer)
        main_layout.addLayout(images_layout)

        self.relative_zoom = 1.0
        self.shared_offset = QPoint(0, 0)

        def get_relative():
            return self.relative_zoom, self.shared_offset

        def set_relative(z, o):
            self.relative_zoom = z
            self.shared_offset = o
            self.left_viewer.update()
            self.right_viewer.update()

        self.left_viewer.set_shared(get_relative, set_relative)
        self.right_viewer.set_shared(get_relative, set_relative)

        meta_btn = QHBoxLayout()
        left_side = QVBoxLayout()
        self.left_meta = QLabel()
        self.left_meta.setWordWrap(True)
        self.btn_delete_left = QPushButton("🗑 Delete Left")
        self.btn_move_right = QPushButton("Move -->")
        left_side.addWidget(self.left_meta)
        left_side.addWidget(self.btn_delete_left)
        left_side.addWidget(self.btn_move_right)
        meta_btn.addLayout(left_side)

        right_side = QVBoxLayout()
        self.right_meta = QLabel()
        self.right_meta.setWordWrap(True)
        self.btn_delete_right = QPushButton("🗑 Delete Right")
        self.btn_move_left = QPushButton("Move <--")
        right_side.addWidget(self.right_meta)
        right_side.addWidget(self.btn_delete_right)
        right_side.addWidget(self.btn_move_left)
        meta_btn.addLayout(right_side)
        main_layout.addLayout(meta_btn)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("← Previous")
        self.btn_swap = QPushButton("⇄ Swap")
        self.btn_next = QPushButton("Next →")
        nav.addStretch()
        nav.addWidget(self.btn_prev)
        nav.addStretch()
        nav.addWidget(self.btn_swap)
        nav.addStretch()
        nav.addWidget(self.btn_next)
        nav.addStretch()
        main_layout.addLayout(nav)

        self.btn_delete_left.clicked.connect(lambda: self.delete_image(self.get_current_left()))
        self.btn_delete_right.clicked.connect(lambda: self.delete_image(self.get_current_right()))
        self.btn_move_right.clicked.connect(self.move_left_to_right_folder)
        self.btn_move_left.clicked.connect(self.move_right_to_left_folder)
        self.btn_prev.clicked.connect(self.prev_pair)
        self.btn_next.clicked.connect(self.next_pair)
        self.btn_swap.clicked.connect(self.swap_images)

        QShortcut(QKeySequence(Qt.Key_Left), self, self.prev_pair)
        QShortcut(QKeySequence(Qt.Key_Right), self, self.next_pair)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

        self.update_pair()

    def get_detailed_meta(self, img: ImageInfo) -> str:
        lines = [
            f"<b>Folder:</b> {img.folder}",
            f"<b>Filename:</b> {img.filename}",
            f"<b>Dimensions:</b> {img.width} × {img.height}",
            f"<b>Size:</b> {img.filesize / (1024 * 1024):.2f} MB",
        ]
        try:
            with PILImage.open(img.path) as pil:
                mode = pil.mode
                bit_depth = {
                    "RGB": "24-bit", "RGBA": "32-bit", "L": "8-bit", "CMYK": "32-bit",
                }.get(mode, f"{mode} mode")
                lines.append(f"<b>Bit depth:</b> {bit_depth}")
                exif = pil.getexif()
                if exif:
                    date_taken = exif.get(36867) or exif.get(306)
                    if date_taken:
                        lines.append(f"<b>Date taken:</b> {date_taken}")
                dpi = pil.info.get("dpi")
                if dpi:
                    lines.append(f"<b>DPI:</b> {dpi[0]} x {dpi[1]}")
        except Exception:
            pass
        return "<br>".join(lines)

    def get_current_left(self) -> ImageInfo:
        return self.matches[self.current_idx][1]

    def get_current_right(self) -> ImageInfo:
        return self.matches[self.current_idx][2]

    def update_pair(self):
        if not self.matches:
            self.header.setText("No more pairs")
            return
        if self.current_idx >= len(self.matches):
            self.current_idx = len(self.matches) - 1
        distance, img1, img2 = self.matches[self.current_idx]
        self.header.setText(
            f"Pair {self.current_idx + 1} / {len(self.matches)} — Distance: {distance}"
        )
        self.left_viewer.set_image(img1.path)
        self.right_viewer.set_image(img2.path)
        self.left_viewer.fit_to_widget()
        self.right_viewer.fit_to_widget()

        lp, rp = img1.width * img1.height, img2.width * img2.height
        self.left_viewer.set_highlight(lp > rp)
        self.right_viewer.set_highlight(rp > lp)

        self.left_meta.setText(self.get_detailed_meta(img1))
        self.right_meta.setText(self.get_detailed_meta(img2))

    def swap_images(self):
        if not self.matches:
            return
        saved_rel, saved_off = self.relative_zoom, self.shared_offset
        d, a, b = self.matches[self.current_idx]
        self.matches[self.current_idx] = (d, b, a)
        distance, img1, img2 = self.matches[self.current_idx]
        self.header.setText(
            f"Pair {self.current_idx + 1} / {len(self.matches)} — Distance: {distance}"
        )
        self.left_viewer.set_image(img1.path)
        self.right_viewer.set_image(img2.path)
        self.left_viewer.compute_base_zoom()
        self.right_viewer.compute_base_zoom()
        self.relative_zoom, self.shared_offset = saved_rel, saved_off
        self.left_viewer.update()
        self.right_viewer.update()
        lp, rp = img1.width * img1.height, img2.width * img2.height
        self.left_viewer.set_highlight(lp > rp)
        self.right_viewer.set_highlight(rp > lp)
        self.left_meta.setText(self.get_detailed_meta(img1))
        self.right_meta.setText(self.get_detailed_meta(img2))

    def next_pair(self):
        if not self.matches:
            return
        self.current_idx = (self.current_idx + 1) % len(self.matches)
        self.update_pair()

    def prev_pair(self):
        if not self.matches:
            return
        self.current_idx = (self.current_idx - 1) % len(self.matches)
        self.update_pair()

    def remove_pairs_containing(self, img: ImageInfo):
        img_id = img.id
        self.matches[:] = [
            (d, a, b) for d, a, b in self.matches
            if a.id != img_id and b.id != img_id
        ]
        # Keep master list in MainWindow in sync
        if self.parent_window and hasattr(self.parent_window, "all_matches"):
            self.parent_window.all_matches[:] = [
                (d, a, b) for d, a, b in self.parent_window.all_matches
                if a.id != img_id and b.id != img_id
            ]
        if self.list_window is not None:
            self.list_window.matches = self.matches
            self.list_window.rebuild_table()

        if not self.matches:
            QMessageBox.information(self, "Done", "No more pairs left.")
            self.close()
            return
        if self.current_idx >= len(self.matches):
            self.current_idx = len(self.matches) - 1
        self.update_pair()

    def delete_image(self, img: ImageInfo):
        try:
            if os.path.exists(img.path):
                os.remove(img.path)
            self.remove_pairs_containing(img)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Delete failed: {e}")

    def move_left_to_right_folder(self):
        left, right = self.get_current_left(), self.get_current_right()
        self._move_and_cleanup(left, right.folder)

    def move_right_to_left_folder(self):
        left, right = self.get_current_left(), self.get_current_right()
        self._move_and_cleanup(right, left.folder)

    def _move_and_cleanup(self, img_to_move: ImageInfo, target_folder: str):
        try:
            new_path = os.path.join(target_folder, os.path.basename(img_to_move.path))
            if os.path.exists(new_path):
                QMessageBox.warning(self, "Exists", f"Target already exists:\n{new_path}")
                return
            os.makedirs(target_folder, exist_ok=True)
            os.rename(img_to_move.path, new_path)
            other = (
                self.get_current_right()
                if img_to_move.id == self.get_current_left().id
                else self.get_current_left()
            )
            # Update in-memory record then delete the other file
            img_to_move.path = new_path
            img_to_move.folder = target_folder
            img_to_move.filename = os.path.basename(new_path)
            self.delete_image(other)
        except Exception as e:
            QMessageBox.critical(self, "Move failed", str(e))


# ---------------------------------------------------------------------------
# Pair list
# ---------------------------------------------------------------------------

class PairListWindow(QMainWindow):
    def __init__(self, matches: List[Tuple], title="Duplicate pairs", parent=None):
        super().__init__(parent)
        self.matches = matches
        self.parent_window = parent
        self.selected_ids: Set[str] = set()
        self.setWindowTitle(title)
        self.resize(1600, 850)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.header_label = QLabel()
        self.header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.header_label)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Sel L", "Distance", "Left path", "Left size",
            "Right path", "Right size", "Sel R",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        h.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self.open_selected_row)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_compare = QPushButton("Compare selected row")
        self.btn_compare.clicked.connect(self.open_selected_row)
        self.btn_sel_left = QPushButton("Select all Left")
        self.btn_sel_left.clicked.connect(lambda: self.select_column("left"))
        self.btn_sel_right = QPushButton("Select all Right")
        self.btn_sel_right.clicked.connect(lambda: self.select_column("right"))
        self.btn_clear = QPushButton("Clear selection")
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_delete = QPushButton("🗑 Delete selected images")
        self.btn_delete.clicked.connect(self.delete_selected_images)
        for b in (self.btn_compare, self.btn_sel_left, self.btn_sel_right, self.btn_clear):
            btn_row.addWidget(b)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_delete)
        layout.addLayout(btn_row)

        self.rebuild_table()

    def rebuild_table(self):
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.matches))
        for row, (distance, img1, img2) in enumerate(self.matches):
            left_cb = QTableWidgetItem()
            left_cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            left_cb.setCheckState(Qt.Checked if img1.id in self.selected_ids else Qt.Unchecked)
            left_cb.setData(Qt.UserRole, ("left", row, img1.id))
            self.table.setItem(row, 0, left_cb)

            dist_item = QTableWidgetItem()
            dist_item.setData(Qt.DisplayRole, distance)
            dist_item.setData(Qt.UserRole, row)
            dist_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, dist_item)

            self.table.setItem(row, 2, QTableWidgetItem(img1.path))
            self.table.item(row, 2).setData(Qt.UserRole, row)

            ld = QTableWidgetItem(f"{img1.width}×{img1.height}")
            ld.setTextAlignment(Qt.AlignCenter)
            ld.setData(Qt.UserRole, row)
            self.table.setItem(row, 3, ld)

            self.table.setItem(row, 4, QTableWidgetItem(img2.path))
            self.table.item(row, 4).setData(Qt.UserRole, row)

            rd = QTableWidgetItem(f"{img2.width}×{img2.height}")
            rd.setTextAlignment(Qt.AlignCenter)
            rd.setData(Qt.UserRole, row)
            self.table.setItem(row, 5, rd)

            right_cb = QTableWidgetItem()
            right_cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            right_cb.setCheckState(Qt.Checked if img2.id in self.selected_ids else Qt.Unchecked)
            right_cb.setData(Qt.UserRole, ("right", row, img2.id))
            self.table.setItem(row, 6, right_cb)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.header_label.setText(
            f"{len(self.matches)} pairs   |   {len(self.selected_ids)} images selected"
        )
        try:
            self.table.itemChanged.disconnect(self.on_item_changed)
        except TypeError:
            pass
        self.table.itemChanged.connect(self.on_item_changed)

    def on_item_changed(self, item: QTableWidgetItem):
        data = item.data(Qt.UserRole)
        if not data or not isinstance(data, tuple) or data[0] not in ("left", "right"):
            return
        side, row, img_id = data
        checked = item.checkState() == Qt.Checked
        if checked:
            other_col = 6 if side == "left" else 0
            other = self.table.item(row, other_col)
            if other and other.checkState() == Qt.Checked:
                self.table.blockSignals(True)
                other.setCheckState(Qt.Unchecked)
                od = other.data(Qt.UserRole)
                if od:
                    self.selected_ids.discard(od[2])
                self.table.blockSignals(False)
            self.selected_ids.add(img_id)
        else:
            self.selected_ids.discard(img_id)
        self.header_label.setText(
            f"{len(self.matches)} pairs   |   {len(self.selected_ids)} images selected"
        )

    def select_column(self, side: str):
        col = 0 if side == "left" else 6
        other_col = 6 if side == "left" else 0
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, col)
            other = self.table.item(row, other_col)
            if not item:
                continue
            data = item.data(Qt.UserRole)
            if not data:
                continue
            if other and other.checkState() == Qt.Checked:
                continue
            item.setCheckState(Qt.Checked)
            self.selected_ids.add(data[2])
        self.table.blockSignals(False)
        self.header_label.setText(
            f"{len(self.matches)} pairs   |   {len(self.selected_ids)} images selected"
        )

    def clear_selection(self):
        self.selected_ids.clear()
        self.rebuild_table()

    def open_selected_row(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Select a row first.")
            return
        row = selected[0].row()
        dist_item = self.table.item(row, 1)
        if not dist_item:
            return
        original_idx = dist_item.data(Qt.UserRole)
        if original_idx is None or original_idx >= len(self.matches):
            return
        win = ComparisonWindow(self.matches, self.windowTitle(), self.parent_window)
        win.list_window = self
        win.current_idx = original_idx
        win.update_pair()
        win.show()

    def delete_selected_images(self):
        if not self.selected_ids:
            QMessageBox.information(self, "Nothing selected", "Select images first.")
            return
        reply = QMessageBox.question(
            self,
            "Confirm delete",
            f"Delete {len(self.selected_ids)} selected image(s) from disk?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        id_to_img = {}
        for _, a, b in self.matches:
            id_to_img[a.id] = a
            id_to_img[b.id] = b

        deleted = 0
        errors = []
        for img_id in list(self.selected_ids):
            img = id_to_img.get(img_id)
            if not img:
                continue
            try:
                if os.path.exists(img.path):
                    os.remove(img.path)
                deleted += 1
            except Exception as e:
                errors.append(f"{img.filename}: {e}")

        self.matches[:] = [
            (d, a, b) for d, a, b in self.matches
            if a.id not in self.selected_ids and b.id not in self.selected_ids
        ]
        if self.parent_window and hasattr(self.parent_window, "all_matches"):
            self.parent_window.all_matches[:] = [
                (d, a, b) for d, a, b in self.parent_window.all_matches
                if a.id not in self.selected_ids and b.id not in self.selected_ids
            ]

        self.selected_ids.clear()
        self.rebuild_table()
        msg = f"Deleted {deleted} image(s)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Done", msg)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Similar and Duplicate Finder")
        self.resize(900, 600)

        self.roots: List[str] = []
        self.images: List[ImageInfo] = []
        self.all_matches: List[Tuple] = []

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        title = QLabel("Similar and Duplicate Finder")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; margin: 8px;")
        layout.addWidget(title)

        layout.addWidget(QLabel("Folders to scan:"))
        self.folder_list = QListWidget()
        layout.addWidget(self.folder_list)

        row = QHBoxLayout()
        btn_add = QPushButton("Add folder…")
        btn_add.clicked.connect(self.add_folder)
        btn_clear = QPushButton("Clear folders")
        btn_clear.clicked.connect(self.clear_folders)
        row.addWidget(btn_add)
        row.addWidget(btn_clear)
        layout.addLayout(row)

        thr = QHBoxLayout()
        thr.addWidget(QLabel("Review max distance:"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 20)
        self.threshold_spin.setValue(8)
        thr.addWidget(self.threshold_spin)
        thr.addWidget(QLabel("(scan ceiling is always 12)"))
        thr.addStretch()
        layout.addLayout(thr)

        self.btn_run = QPushButton("Scan and Find Duplicates")
        self.btn_run.clicked.connect(self.start_pipeline)
        layout.addWidget(self.btn_run)

        self.stats = QLabel("No scan yet")
        layout.addWidget(self.stats)

        self.btn_review = QPushButton("Review duplicates")
        self.btn_review.clicked.connect(self.open_review)
        self.btn_review.setEnabled(False)
        layout.addWidget(self.btn_review)

        layout.addStretch()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder and folder not in self.roots:
            self.roots.append(folder)
            self.folder_list.addItem(folder)

    def clear_folders(self):
        self.roots.clear()
        self.folder_list.clear()

    def start_pipeline(self):
        if not self.roots:
            QMessageBox.warning(self, "No folders", "Add at least one folder.")
            return
        self.btn_run.setEnabled(False)
        self.btn_review.setEnabled(False)
        self.progress = QProgressDialog("Scanning images…", None, 0, 100, self)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.show()

        self.scan_worker = ScanWorker(self.roots)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.finished.connect(self.on_scan_finished)
        self.scan_worker.error.connect(self.on_error)
        self.scan_worker.start()

    def on_scan_progress(self, current, total, path):
        if total:
            self.progress.setMaximum(total)
            self.progress.setValue(current)
            self.progress.setLabelText(f"Scanning… {current}/{total}\n{path}")

    def on_scan_finished(self, images: List[ImageInfo]):
        self.images = images
        self.progress.setLabelText(f"Found {len(images)} images. Comparing pairs…")
        self.progress.setValue(0)
        self.progress.setMaximum(100)
        self.match_worker = MatchWorker(images, threshold=12)
        self.match_worker.progress.connect(self.on_match_progress)
        self.match_worker.finished.connect(self.on_match_finished)
        self.match_worker.error.connect(self.on_error)
        self.match_worker.start()

    def on_match_progress(self, current, total):
        if total:
            self.progress.setMaximum(total)
            self.progress.setValue(current)
            self.progress.setLabelText(f"Comparing… {current}/{total}")

    def on_match_finished(self, matches):
        self.progress.close()
        self.btn_run.setEnabled(True)
        self.all_matches = matches
        max_d = self.threshold_spin.value()
        visible = sum(1 for d, _, _ in matches if d <= max_d)
        self.stats.setText(
            f"Images: {len(self.images)}\n"
            f"Pairs ≤ {max_d}: {visible}\n"
            f"Pairs ≤ 12: {len(matches)}"
        )
        self.btn_review.setEnabled(bool(matches))
        QMessageBox.information(self, "Done", f"Found {len(matches)} pairs (distance ≤ 12).")

    def on_error(self, msg):
        self.progress.close()
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)

    def open_review(self):
        max_d = self.threshold_spin.value()
        filtered = [m for m in self.all_matches if m[0] <= max_d]
        if not filtered:
            QMessageBox.information(self, "None", f"No pairs with distance ≤ {max_d}.")
            return
        win = PairListWindow(filtered, f"Duplicates (≤ {max_d})", self)
        win.show()


if __name__ == "__main__":
    import traceback

    def excepthook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)
        QMessageBox.critical(None, "Crash", str(exc_value))

    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())