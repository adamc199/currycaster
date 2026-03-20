#!/usr/bin/env python3
print("DEBUG: Starting Broadcast System (v41.5 - Fixed PFL Race Condition)...")
import sys, subprocess, struct, json, os, time, gi, mido, pulsectl
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QFileDialog, QLabel, QFrame, QSlider, QTreeView, QMenu,
                             QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                             QTabWidget, QGridLayout, QInputDialog, QColorDialog, QMainWindow,
                             QFontDialog)
from PyQt6.QtCore import (QTimer, Qt, pyqtSignal, QDir, QThread, QObject, QSortFilterProxyModel, QMimeData, QUrl, QTime)
from PyQt6.QtGui import (QPainter, QColor, QPen, QAction, QFileSystemModel, QShortcut, QKeySequence, QDrag, QFont)

gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import Gst, GObject, GstPbutils

# --- CONFIGURATION PATHS ---
USER_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "currycaster")
os.makedirs(USER_CONFIG_DIR, exist_ok=True)
MIDI_CONFIG_FILE = os.path.join(USER_CONFIG_DIR, "midi_config.json")
AUDIO_CONFIG_FILE = os.path.join(USER_CONFIG_DIR, "audio_config.json")
LIBRARY_INDEX_FILE = os.path.join(USER_CONFIG_DIR, "library_index.json")
CART_CONFIG_FILE = os.path.join(USER_CONFIG_DIR, "cart_config.json")
WINDOW_LAYOUT_FILE = os.path.join(USER_CONFIG_DIR, "window_layout.json")
EXPLORER_CONFIG_FILE = os.path.join(USER_CONFIG_DIR, "explorer_config.json")
VALID_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac', '.wma'}

# --- GLOBAL HELPERS ---
def get_filepath_from_drop(e):
    if e.mimeData().hasUrls():
        for url in e.mimeData().urls():
            if url.isLocalFile(): return url.toLocalFile()
    if e.mimeData().hasText():
        return e.mimeData().text().replace("file://", "").strip()
    return None

def get_log_volume(linear_value):
    """Converts 0-100 linear to 0.0-1.0 cubic gain."""
    return (linear_value / 100.0) ** 3

# --- Window State Manager ---
class WindowStateManager:
    def __init__(self):
        self.data = {}
        self.load()
    def load(self):
        if os.path.exists(WINDOW_LAYOUT_FILE):
            try:
                with open(WINDOW_LAYOUT_FILE, 'r') as f: self.data = json.load(f)
            except: pass
    def save(self):
        try:
            with open(WINDOW_LAYOUT_FILE, 'w') as f: json.dump(self.data, f, indent=4)
        except: pass
    def apply(self, widget, name):
        if name in self.data:
            g = self.data[name]
            widget.move(g['x'], g['y']); widget.resize(g['w'], g['h'])
    def record(self, widget, name):
        self.data[name] = {'x': widget.x(), 'y': widget.y(), 'w': widget.width(), 'h': widget.height()}
        self.save()

window_manager = WindowStateManager()

# --- 1. SEARCH INDEXER WORKER ---
class FileIndexerWorker(QThread):
    index_finished = pyqtSignal(list)
    def __init__(self, root_path):
        super().__init__(); self.root_path = root_path
    def run(self):
        db = []
        try:
            for root, dirs, files in os.walk(self.root_path):
                if any(x in root for x in ["/.", "/found.", "$RECYCLE"]): continue
                for file in files:
                    if file.startswith('.') or os.path.splitext(file)[1].lower() not in VALID_EXTENSIONS: continue
                    try:
                        fp = os.path.join(root, file)
                        db.append((file, fp, os.path.getmtime(fp)))
                    except: pass
        except: pass
        db.sort(key=lambda x: x[2], reverse=True)
        self.index_finished.emit(db)

# --- 2. AUDIO ROUTER ---
class AudioRouter:
    def __init__(self):
        self.pulse = pulsectl.Pulse('currycaster-router')
        self.active_players, self.active_cart_ids = [], []
        self.sinks = self.refresh_devices()
        self.global_pgm_sink = self.sinks[0].name if self.sinks else None
        self.global_cue_sink = self.sinks[0].name if self.sinks else None
        self.pending_routes = {}
        self.load_config()
    def refresh_devices(self):
        self.sinks = self.pulse.sink_list(); return self.sinks
    def load_config(self):
        if not os.path.exists(AUDIO_CONFIG_FILE): return
        try:
            with open(AUDIO_CONFIG_FILE, 'r') as f:
                d = json.load(f)
                an = [s.name for s in self.sinks]
                if d.get("program_sink") in an: self.global_pgm_sink = d["program_sink"]
                if d.get("cue_sink") in an: self.global_cue_sink = d["cue_sink"]
        except: pass
    def save_config(self):
        try:
            with open(AUDIO_CONFIG_FILE, 'w') as f:
                json.dump({"program_sink": self.global_pgm_sink, "cue_sink": self.global_cue_sink}, f, indent=4)
        except: pass
    def set_program_device(self, name): self.global_pgm_sink = name; self.save_config(); self.move_all_streams()
    def set_cue_device(self, name): self.global_cue_sink = name; self.save_config(); self.move_all_streams()
    def register_player(self, p):
        if p not in self.active_players: self.active_players.append(p)
    def register_cart_id(self, cid):
        if cid not in self.active_cart_ids: self.active_cart_ids.append(cid)
    def move_all_streams(self):
        for p in self.active_players: self.route_stream(f"Currycaster_Player_{p.player_id}", p.pfl)
        for cid in self.active_cart_ids: self.route_stream(cid, False)
    def route_stream(self, app_id, is_cue, retries=8):
        if app_id in self.pending_routes:
            self.pending_routes[app_id] = False
        target = self.global_cue_sink if is_cue else self.global_pgm_sink
        self.pending_routes[app_id] = True
        def attempt(rem):
            if not self.pending_routes.get(app_id, False):
                return
            try:
                t_snk = next((s for s in self.pulse.sink_list() if s.name == target), None)
                t_str = next((i for i in self.pulse.sink_input_list() if i.proplist.get('application.name') == app_id), None)
                if t_str and t_snk:
                    if t_str.sink != t_snk.index:
                        self.pulse.sink_input_move(t_str.index, t_snk.index)
                        self.pending_routes[app_id] = False
                    else:
                        self.pending_routes[app_id] = False
                elif rem > 0: QTimer.singleShot(150, lambda: attempt(rem - 1))
            except: pass
        attempt(retries)
    def route_player(self, pid, is_cue): self.route_stream(f"Currycaster_Player_{pid}", is_cue)

# --- 3. MIDI ENGINE ---
class MidiWorker(QThread):
    midi_signal = pyqtSignal(str, int, int, int)
    def __init__(self):
        super().__init__(); self.running = True; self.port_name = None
    def run(self):
        try:
            av = mido.get_input_names()
            self.port_name = next((n for n in av if any(x in n.lower() for x in ["nano", "kontrol"])), av[0] if av else None)
            if not self.port_name: return
            with mido.open_input(self.port_name) as port:
                while self.running:
                    for m in port.iter_pending():
                        if m.type == 'control_change': self.midi_signal.emit('cc', m.channel, m.control, m.value)
                        elif m.type == 'note_on': self.midi_signal.emit('note', m.channel, m.note, m.velocity)
                    self.msleep(5)
        except: pass
    def stop(self): self.running = False; self.wait()

class MidiMapper(QObject):
    def __init__(self):
        super().__init__(); self.l_map, self.r_map, self.registry = {}, {}, {}
        self.learning_uid = None; self.load()
    def load(self):
        if os.path.exists(MIDI_CONFIG_FILE):
            try:
                with open(MIDI_CONFIG_FILE, 'r') as f: self.l_map = json.load(f)
            except: pass
    def save(self):
        try:
            with open(MIDI_CONFIG_FILE, 'w') as f: json.dump(self.l_map, f, indent=4)
        except: pass
    def register(self, uid, cb):
        self.registry[uid] = cb
        if uid in self.l_map: self.r_map[self.l_map[uid]] = cb
    def handle(self, mt, ch, idx, val):
        uk = f"{mt}:{ch}:{idx}"
        if self.learning_uid and val > 0:
            self.l_map[self.learning_uid] = uk; self.save()
            self.r_map[uk] = self.registry[self.learning_uid]; self.learning_uid = None
            return
        if uk in self.r_map:
            try: self.r_map[uk](val)
            except: pass
    def start_learning(self, uid): self.learning_uid = uid

# --- 4. UI COMPONENTS ---
class MidiButton(QPushButton):
    def __init__(self, t, uid, mm, cb): super().__init__(t); self.uid = uid; self.mm = mm; mm.register(uid, lambda v: cb() if v > 0 else None)
    def contextMenuEvent(self, e):
        m = QMenu(self); m.addAction(f"MIDI Learn ({self.uid})", lambda: self.mm.start_learning(self.uid)); m.exec(e.globalPos())

class MidiSlider(QSlider):
    def __init__(self, o, uid, mm, cb): super().__init__(o); self.uid = uid; self.mm = mm; mm.register(uid, lambda v: cb(int((v/127.0)*100)))
    def contextMenuEvent(self, e):
        m = QMenu(self); m.addAction(f"MIDI Learn ({self.uid})", lambda: self.mm.start_learning(self.uid)); m.exec(e.globalPos())

class ClickableLabel(QLabel):
    clicked = pyqtSignal(); right_clicked = pyqtSignal()
    def mousePressEvent(self, e): (e.button()==Qt.MouseButton.LeftButton and self.clicked.emit()) or (e.button()==Qt.MouseButton.RightButton and self.right_clicked.emit())

class DraggableListWidget(QListWidget):
    def startDrag(self, a):
        i = self.currentItem()
        if i:
            m = QMimeData(); m.setText(i.data(Qt.ItemDataRole.UserRole)); m.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole))])
            d = QDrag(self); d.setMimeData(m); d.exec(Qt.DropAction.CopyAction)

# --- 5. WAVEFORM WIDGET ---
class WaveformWidget(QFrame):
    seek_requested = pyqtSignal(float)
    cue_points_changed = pyqtSignal(float, float)
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(80)
        self.setStyleSheet("background-color: #222; border: 1px solid #444;")
        self.waveform_data, self.position_percent = [], 0.0
        self.cue_in, self.cue_out = 0.0, 1.0
        self.zoom_level, self.view_offset = 1.0, 0.0
        self.last_mouse_x, self.is_panning, self.has_moved = 0, False, False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    def load_waveform_from_file(self, file_path):
        self.waveform_data, self.zoom_level, self.view_offset = [], 1.0, 0.0
        self.cue_in, self.cue_out = 0.0, 1.0
        self.cue_points_changed.emit(0.0, 1.0)
        cmd = ['ffmpeg', '-i', file_path, '-f', 's16le', '-ac', '1', '-acodec', 'pcm_s16le', '-ar', '200', '-vn', '-']
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if result.stdout:
                total_samples = len(result.stdout) // 2
                fmt = f"<{total_samples}h"
                samples = struct.unpack(fmt, result.stdout)
                abs_values = [abs(s) for s in samples]
                max_val = max(abs_values) if abs_values else 0
                self.waveform_data = [s / max_val if max_val > 0 else 0 for s in abs_values]
                self.update()
        except: pass
    def clear_waveform(self): self.waveform_data, self.position_percent = [], 0.0; self.update()
    def set_position(self, percent): self.position_percent = percent; self.update()
    def set_start_point(self, percent): self.cue_in = max(0.0, min(percent, self.cue_out)); self.cue_points_changed.emit(self.cue_in, self.cue_out); self.update()
    def set_end_point(self, percent): self.cue_out = min(1.0, max(percent, self.cue_in)); self.cue_points_changed.emit(self.cue_in, self.cue_out); self.update()
    def reset_clip(self): self.cue_in, self.cue_out = 0.0, 1.0; self.cue_points_changed.emit(0.0, 1.0); self.update()
    def wheelEvent(self, event):
        if not self.waveform_data: return
        delta = event.angleDelta().y(); zoom_factor = 1.1 if delta > 0 else 0.9
        new_zoom = max(1.0, min(50.0, self.zoom_level * zoom_factor))
        mx, w = event.position().x(), self.width()
        tp_old = w * self.zoom_level
        mouse_pct = (mx + self.view_offset) / tp_old if tp_old > 0 else 0
        self.zoom_level = new_zoom
        self.view_offset = max(0, min((mouse_pct * w * self.zoom_level) - mx, (w * self.zoom_level) - w))
        self.update()
    def mousePressEvent(self, event):
        self.last_mouse_x, self.has_moved = event.position().x(), False
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(max(0.0, min(1.0, (event.position().x() + self.view_offset) / (self.width() * self.zoom_level))))
        elif event.button() == Qt.MouseButton.RightButton:
            self.is_panning = True; self.setCursor(Qt.CursorShape.ClosedHandCursor)
    def mouseMoveEvent(self, event):
        if self.is_panning:
            dx = event.position().x() - self.last_mouse_x
            if abs(dx) > 2: self.has_moved = True
            self.last_mouse_x = event.position().x()
            self.view_offset = max(0, min(self.view_offset - dx, (self.width() * self.zoom_level) - self.width()))
            self.update()
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.is_panning = False; self.setCursor(Qt.CursorShape.PointingHandCursor)
            if not self.has_moved:
                pct = max(0.0, min(1.0, (event.position().x() + self.view_offset) / (self.width() * self.zoom_level)))
                self.show_context_menu(event.globalPosition().toPoint(), pct)
    def show_context_menu(self, global_pos, percent):
        m = QMenu(self)
        m.addAction("Set Start (Cue In)", lambda: self.set_start_point(percent))
        m.addAction("Set End (Cue Out)", lambda: self.set_end_point(percent))
        m.addSeparator(); m.addAction("Reset Clip", self.reset_clip); m.exec(global_pos)
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid_h = h / 2
        if not self.waveform_data: return
        p_br, p_dm = QPen(QColor("#00bcd4")), QPen(QColor("#444444"))
        num_samples = len(self.waveform_data)
        virtual_width = w * self.zoom_level
        step = virtual_width / num_samples
        for i in range(max(0, int(self.view_offset / step)), min(num_samples, int((self.view_offset + w) / step) + 1)):
            x = (i * step) - self.view_offset
            amp = self.waveform_data[i] * mid_h * 0.95
            painter.setPen(p_br if self.cue_in <= i/num_samples <= self.cue_out else p_dm)
            painter.drawLine(int(x), int(mid_h - amp), int(x), int(mid_h + amp))
        for color, pct in [("#00ff00", self.cue_in), ("#ff0000", self.cue_out), ("#ffffff", self.position_percent)]:
            x = (pct * virtual_width) - self.view_offset
            if 0 <= x <= w:
                painter.setPen(QPen(QColor(color), 2)); painter.drawLine(int(x), 0, int(x), h)

# --- 6. PLAYER MODULE ---
class PlayerModule(QWidget):
    def __init__(self, pid, mm, ar):
        super().__init__(); self.setAcceptDrops(True)
        self.player_id, self.mm, self.ar = pid, mm, ar
        self.pfl, self.playing, self.dur = False, False, 0
        self.c_in, self.c_out, self.seek_req = 0.0, 1.0, True
        self.show_remaining = True
        self.ar.register_player(self); Gst.init(None); self.pipeline = None
        self.setStyleSheet("border-right: 1px solid #333; background: #1a1a1a;")
        self.setFixedWidth(240)
        lay = QHBoxLayout(self); lay.setContentsMargins(2,2,2,2)
        l_cnt = QWidget(); l_lay = QVBoxLayout(l_cnt); l_lay.setContentsMargins(0,0,0,0)
        self.vol = MidiSlider(Qt.Orientation.Vertical, f"p{pid}_vol", mm, self.set_v_internal)
        self.vol.setRange(0, 100); self.vol.setValue(100); self.vol.valueChanged.connect(self.set_v_engine)
        self.lbl_t = ClickableLabel("00:00"); self.lbl_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_t.setStyleSheet("font-size:24px; font-weight:bold; color:#ff5555; font-family:monospace;")
        self.lbl_t.clicked.connect(self.toggle_time_mode)
        self.lbl_s = ClickableLabel(f"Player {pid}"); self.lbl_s.setWordWrap(True)
        self.lbl_s.setStyleSheet("color:#eee; font-size:12px; font-weight:bold; min-height:40px;")
        self.lbl_s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_s.clicked.connect(self.toggle_pfl); self.lbl_s.right_clicked.connect(self.open_menu)
        self.wf = WaveformWidget(); self.wf.seek_requested.connect(self.seek_audio); self.wf.cue_points_changed.connect(self.upd_clip)
        ctrls = QHBoxLayout()
        self.b_load = QPushButton("LOAD"); self.b_play = MidiButton("PLAY", f"p{pid}_play", mm, self.toggle_play)
        self.b_stop = MidiButton("STOP", f"p{pid}_stop", mm, self.stop_audio); self.b_dump = MidiButton("DUMP", f"p{pid}_dump", mm, self.dump_track)
        for b in [self.b_load, self.b_play, self.b_stop, self.b_dump]: b.setStyleSheet("background:#333; color:white; font-size:10px; padding:6px;")
        self.b_load.clicked.connect(self.load_dialog); self.b_play.clicked.connect(self.toggle_play)
        self.b_stop.clicked.connect(self.stop_audio); self.b_dump.clicked.connect(self.dump_track)
        l_lay.addWidget(self.lbl_t); l_lay.addWidget(self.lbl_s); l_lay.addWidget(self.wf)
        ctrls.addWidget(self.b_load); ctrls.addWidget(self.b_play); ctrls.addWidget(self.b_stop); ctrls.addWidget(self.b_dump)
        l_lay.addLayout(ctrls); lay.addWidget(l_cnt); lay.addWidget(self.vol)
        self.tmr = QTimer(); self.tmr.timeout.connect(self.update_ui)
    def dragEnterEvent(self, event): (event.accept() if get_filepath_from_drop(event) else event.ignore())
    def dropEvent(self, event): 
        path = get_filepath_from_drop(event)
        if path:
            self.load_track(path)
            event.accept()
    def set_v_internal(self, v): self.vol.setValue(v)
    def set_v_engine(self, v): (self.pipeline and self.pipeline.set_property("volume", get_log_volume(v)))
    def toggle_pfl(self):
        self.pfl = not self.pfl; self.lbl_s.setStyleSheet(f"color:{'#ffa500' if self.pfl else '#eee'}; font-size:12px; font-weight:bold; border:{'1px solid #ffa500' if self.pfl else 'none'};")
        self.ar.route_player(self.player_id, self.pfl)
    def toggle_time_mode(self): self.show_remaining = not self.show_remaining; self.update_ui()
    def upd_clip(self, s, e): self.c_in, self.c_out = s, e; (not self.playing) and (setattr(self, 'seek_req', True) or self.wf.set_position(s))
    def load_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Load Audio", "", "Audio (*.mp3 *.wav *.ogg *.flac)")
        if f: self.load_track(f)
    def load_track(self, path):
        self.stop_audio(); self.lbl_s.setText(os.path.basename(path)); self.wf.load_waveform_from_file(path); self.dur = 0
        try:
            disc = GstPbutils.Discoverer.new(10 * Gst.SECOND)
            info = disc.discover_uri(Path(path).as_uri())
            self.dur = info.get_duration(); self.update_ui_label(0); self.wf.set_position(0.0)
        except Exception as e: print(f"Discoverer error: {e}")
        self.pipeline = Gst.ElementFactory.make("playbin", None); self.pipeline.set_property("uri", Path(path).as_uri())
        sink = Gst.ElementFactory.make("pulsesink", None)
        props = Gst.Structure.new_empty("props"); props.set_value("application.name", f"Currycaster_Player_{self.player_id}"); sink.set_property("stream-properties", props)
        target = self.ar.global_cue_sink if self.pfl else self.ar.global_pgm_sink
        if target: sink.set_property("device", target)
        self.pipeline.set_property("audio-sink", sink); self.pipeline.set_property("volume", get_log_volume(self.vol.value()))
        self.pipeline.set_state(Gst.State.PAUSED); self.playing = False; self.seek_req = True
    def toggle_play(self):
        if not self.pipeline: return
        if self.playing: 
            self.pipeline.set_state(Gst.State.PAUSED)
            self.tmr.stop()
            self.playing = False
            self.b_play.setText("PLAY")
        else:
            if self.seek_req: self.pipeline.set_state(Gst.State.PAUSED); self.enforce_seek()
            self.pipeline.set_state(Gst.State.PLAYING); self.tmr.start(40); self.playing = True; self.b_play.setText("PAUSE")
            self.ar.route_player(self.player_id, self.pfl)
    def enforce_seek(self):
        s, d = self.pipeline.query_duration(Gst.Format.TIME)
        if s: self.dur = d
        if self.dur > 0 and self.c_in > 0: self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(self.c_in * self.dur))
        self.seek_req = False
    def stop_audio(self):
        if not self.pipeline: return
        if self.playing: 
            self.pipeline.set_state(Gst.State.PAUSED)
            self.tmr.stop()
            self.playing = False
            self.b_play.setText("PLAY")
            self.enforce_seek(); self.wf.set_position(self.c_in); self.update_ui_label(int(self.c_in * self.dur))
        else:
            if self.dur > 0: self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
            self.wf.set_position(0.0); self.update_ui_label(0)
    def dump_track(self):
        if self.pipeline: self.pipeline.set_state(Gst.State.NULL)
        self.tmr.stop()
        self.pipeline = None
        self.lbl_t.setText("00:00")
        self.lbl_s.setText(f"Player {self.player_id}")
        self.wf.clear_waveform()
    def seek_audio(self, p):
        if self.pipeline and self.dur > 0:
            if self.playing: self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(p * self.dur))
            else: self.c_in, self.seek_req = p, True
        self.wf.set_position(p)
    def update_ui(self):
        if not self.pipeline: return
        sd, d = self.pipeline.query_duration(Gst.Format.TIME); sp, p = self.pipeline.query_position(Gst.Format.TIME)
        if sd: self.dur = d
        if sp and self.dur > 0:
            if self.seek_req and p < int(self.c_in * self.dur): return
            self.wf.set_position(p / self.dur); self.update_ui_label(p)
            if p >= (int(self.c_out * self.dur) if self.c_out < 1.0 else self.dur): self.stop_audio()
    def update_ui_label(self, p):
        if not self.dur: return
        end_p = int(self.c_out * self.dur) if self.c_out < 1.0 else self.dur
        ts = int((end_p - p if self.show_remaining else p) / 1e9)
        prefix = '-' if self.show_remaining else ''
        self.lbl_t.setText(f"{prefix}{ts//60:02}:{ts%60:02}")
        self.lbl_t.setStyleSheet(f"font-size:24px; font-weight:bold; color:{'#ff5555' if self.show_remaining else '#00bcd4'}; font-family:monospace;")
    def open_menu(self):
        m = QMenu(self); m.addAction("Toggle PFL", self.toggle_pfl); pm, cm = m.addMenu("Program Out"), m.addMenu("Cue Out")
        for s in self.ar.refresh_devices(): pm.addAction(s.description, lambda n=s.name: self.ar.set_program_device(n)); cm.addAction(s.description, lambda n=s.name: self.ar.set_cue_device(n))
        m.exec(self.lbl_s.mapToGlobal(self.lbl_s.rect().center()))
    def is_active(self): return self.pipeline is not None

# --- 7. CART BUTTON ---
class CartButton(QFrame):
    playing_triggered, finished_triggered = pyqtSignal(object), pyqtSignal(object)
    def __init__(self, ar, parent=None):
        super().__init__(parent); self.setAcceptDrops(True); self.setFrameShape(QFrame.Shape.StyledPanel); self.setMinimumHeight(100)
        self.ar, self.file, self.pipeline, self.playing = ar, None, None, False
        self.uid = f"Currycaster_Cart_{id(self)}"; self.ar.register_cart_id(self.uid)
        self.c_name, self.c_color = "", "#333333"
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        self.btn = QPushButton("EMPTY"); self.btn.setSizePolicy(self.btn.sizePolicy().Policy.Expanding, self.btn.sizePolicy().Policy.Expanding)
        self.btn.clicked.connect(self.toggle_play); self.btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.btn.customContextMenuRequested.connect(self.open_context)
        self.vol = QSlider(Qt.Orientation.Vertical); self.vol.setRange(0, 100); self.vol.setValue(100); self.vol.setFixedWidth(12); self.vol.valueChanged.connect(self.upd_vol)
        lay.addWidget(self.btn); lay.addWidget(self.vol)
    def dragEnterEvent(self, event): (event.accept() if get_filepath_from_drop(event) else event.ignore())
    def dropEvent(self, event): path = get_filepath_from_drop(event); (path and self.load_file(path) or event.accept())
    def open_context(self, pos):
        if not self.file: return
        m = QMenu(self); m.addAction("Rename Cart", self.ask_rename); m.addAction("Set Color", self.ask_color); m.addSeparator(); m.addAction("Clear Cart", self.clear_cart); m.exec(self.btn.mapToGlobal(pos))
    def ask_rename(self):
        t, ok = QInputDialog.getText(self, "Rename Cart", "New Name:", text=self.c_name)
        if ok and t: self.c_name = t; self.upd_ui()
    def ask_color(self):
        c = QColorDialog.getColor(initial=QColor(self.c_color), parent=self, title="Cart Color")
        if c.isValid(): self.c_color = c.name(); self.upd_ui()
    def clear_cart(self): self.stop(); self.file, self.c_name, self.c_color = None, "", "#333333"; self.upd_ui()
    
    def wheelEvent(self, event): 
        cur_v = self.vol.value()
        step = 5 if event.angleDelta().y() > 0 else -5
        self.vol.setValue(max(0, min(100, cur_v + step)))
        
    def load_file(self, p, n=None, c=None, v=100): 
        self.file = p
        self.c_name = n or os.path.basename(p)
        self.c_color = c or self.c_color
        self.vol.setValue(v)
        self.upd_ui()
        
    def upd_ui(self):
        self.btn.setText(self.c_name if self.file else "EMPTY")
        color = self.c_color if self.file else '#222'
        text_color = 'white' if self.file else '#555'
        border = '2px solid #00ff00' if self.playing else 'none'
        self.btn.setStyleSheet(f"background:{color}; color:{text_color}; font-weight:bold; font-size:11px; border:{border};")

    def toggle_play(self): self.stop() if self.playing else self.play()
    def play(self):
        if not self.file: return
        self.stop(); self.pipeline = Gst.ElementFactory.make("playbin", None); self.pipeline.set_property("uri", Path(self.file).as_uri())
        sink = Gst.ElementFactory.make("pulsesink", None); props = Gst.Structure.new_empty("props"); props.set_value("application.name", self.uid); sink.set_property("stream-properties", props)
        if self.ar.global_pgm_sink: sink.set_property("device", self.ar.global_pgm_sink)
        self.pipeline.set_property("audio-sink", sink); self.pipeline.set_property("volume", get_log_volume(self.vol.value()))
        bus = self.pipeline.get_bus(); bus.add_signal_watch(); bus.connect("message::eos", lambda b,m: self.stop())
        self.pipeline.set_state(Gst.State.PLAYING); self.playing = True; self.upd_ui(); self.playing_triggered.emit(self)
    def stop(self):
        if self.pipeline: self.pipeline.set_state(Gst.State.NULL); self.pipeline = None
        self.playing = False; self.upd_ui(); self.finished_triggered.emit(self)
    def upd_vol(self): (self.pipeline and self.pipeline.set_property("volume", get_log_volume(self.vol.value())))
    def get_rem_ns(self):
        if self.pipeline:
            s1, p = self.pipeline.query_position(Gst.Format.TIME); s2, d = self.pipeline.query_duration(Gst.Format.TIME)
            if s1 and s2: return d - p
        return 0
    def get_data(self): return {"path": self.file, "name": self.c_name, "color": self.c_color, "vol": self.vol.value()}

# --- 8. FILE EXPLORER ---
class JunkFileFilter(QSortFilterProxyModel):
    def filterAcceptsRow(self, r, p):
        idx = self.sourceModel().index(r, 0, p); fn = self.sourceModel().fileName(idx)
        return not any(fn.startswith(x) for x in ['.', 'found.']) and fn not in ["$RECYCLE.BIN", "Recovery"]

DEFAULT_MEDIA_ROOT = "/run/media/adam/NO AGENDA"

class FileExplorerWindow(QWidget):
    file_selected = pyqtSignal(str)
    def __init__(self):
        super().__init__(); self.setWindowTitle("Library (SSD)"); self.resize(400, 700); self.setStyleSheet("background:#222; color:#eee;")
        window_manager.apply(self, "library_window"); lay = QVBoxLayout(self)
        self.root = self.load_root_path(); self.db, self.is_focused = [], False
        self.font_family, self.font_size = "Sans Serif", 10
        tb = QHBoxLayout(); self.si = QLineEdit(); self.si.setPlaceholderText("Search Library... (Ctrl+F)"); self.si.textChanged.connect(self.on_search)
        self.btn_refresh = QPushButton("Re-Index"); self.btn_refresh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_refresh.customContextMenuRequested.connect(self.show_refresh_menu); self.btn_refresh.clicked.connect(self.start_indexing)
        self.btn_select_folder = QPushButton("..."); self.btn_select_folder.setToolTip("Select Root Folder"); self.btn_select_folder.clicked.connect(self.select_root_folder)
        self.btn_font = QPushButton("Aa"); self.btn_font.clicked.connect(self.choose_font)
        tb.addWidget(self.si); tb.addWidget(self.btn_select_folder); tb.addWidget(self.btn_refresh); tb.addWidget(self.btn_font); lay.addLayout(tb)
        self.btn_focus = QPushButton("Focus on Folder"); self.btn_focus.clicked.connect(self.toggle_focus); lay.addWidget(self.btn_focus)
        self.sm = QFileSystemModel(); self.sm.setRootPath(self.root); self.pm = JunkFileFilter(); self.pm.setSourceModel(self.sm)
        self.tr = QTreeView(); self.tr.setModel(self.pm); self.tr.setDragEnabled(True); self.tr.setRootIndex(self.pm.mapFromSource(self.sm.index(self.root)))
        for i in range(1,4): self.tr.setColumnHidden(i, True)
        self.tr.setHeaderHidden(True); self.tr.clicked.connect(self.on_tree_click); lay.addWidget(self.tr)
        self.rl = DraggableListWidget(); self.rl.setVisible(False); self.rl.itemClicked.connect(self.on_list_click); lay.addWidget(self.rl); self.rl.setDragEnabled(True)
        self.progress = QProgressBar(); self.progress.setVisible(False); self.progress.setRange(0, 0); self.progress.setStyleSheet("QProgressBar { height: 4px; border: none; background: #333; } QProgressBar::chunk { background-color: #00bcd4; }")
        lay.addWidget(self.progress); QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.si.setFocus)
        self.load_cache(); self.load_font_config(); QTimer.singleShot(1000, self.start_indexing)
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.timeout.connect(self.perform_search); self.current_query = ""
        self.update_title()
    def load_root_path(self):
        if os.path.exists(EXPLORER_CONFIG_FILE):
            try:
                with open(EXPLORER_CONFIG_FILE, 'r') as f:
                    d = json.load(f)
                    path = d.get("root_path", DEFAULT_MEDIA_ROOT)
                    if os.path.isdir(path): return path
            except: pass
        return DEFAULT_MEDIA_ROOT
    def save_root_path(self):
        try:
            with open(EXPLORER_CONFIG_FILE, 'r') as f: d = json.load(f)
        except: d = {}
        d["root_path"] = self.root
        try:
            with open(EXPLORER_CONFIG_FILE, 'w') as f: json.dump(d, f, indent=4)
        except: pass
    def select_root_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Root Folder", self.root)
        if folder and os.path.isdir(folder):
            self.root = folder
            self.save_root_path()
            self.sm.setRootPath(self.root)
            self.tr.setRootIndex(self.pm.mapFromSource(self.sm.index(self.root)))
            self.load_cache()
            QTimer.singleShot(100, self.start_indexing)
            self.update_title()
    def update_title(self):
        short = self.root.split("/")[-1] if self.root else "Library"
        self.setWindowTitle(f"Library ({short})")
    def load_cache(self):
        if os.path.exists(LIBRARY_INDEX_FILE):
            try:
                with open(LIBRARY_INDEX_FILE, 'r') as f: self.db = json.load(f)
            except: pass
    def load_font_config(self):
        if os.path.exists(EXPLORER_CONFIG_FILE):
            try:
                with open(EXPLORER_CONFIG_FILE, 'r') as f:
                    d = json.load(f); self.font_family, self.font_size = d.get("family", "Sans Serif"), d.get("size", 10)
            except: pass
        self.apply_font()
    def save_font_config(self):
        with open(EXPLORER_CONFIG_FILE, 'w') as f: json.dump({"family": self.font_family, "size": self.font_size}, f)
    def choose_font(self):
        f, ok = QFontDialog.getFont(QFont(self.font_family, self.font_size), self, "Select Library Font")
        if ok: self.font_family, self.font_size = f.family(), f.pointSize(); self.apply_font(); self.save_font_config()
    def apply_font(self):
        ss = f"QTreeView, QListWidget {{ background-color: #1a1a1a; border: none; font-family: '{self.font_family}'; font-size: {self.font_size}pt; }} QTreeView::item, QListWidget::item {{ padding: 5px; }} QTreeView::item:hover, QListWidget::item:hover {{ background: #333; }} QTreeView::item:selected, QListWidget::item:selected {{ background: #00bcd4; color: black; }}"
        self.tr.setStyleSheet(ss); self.rl.setStyleSheet(ss)
    def show_refresh_menu(self, pos):
        m = QMenu(self); m.addAction("Clear Library Cache", self.clear_cache); m.exec(self.btn_refresh.mapToGlobal(pos))
    def clear_cache(self):
        if os.path.exists(LIBRARY_INDEX_FILE): os.remove(LIBRARY_INDEX_FILE)
        self.db = []; self.start_indexing()
    def start_indexing(self):
        self.btn_refresh.setText("Scanning..."); self.progress.setVisible(True); self.worker = FileIndexerWorker(self.root)
        self.worker.index_finished.connect(self.on_idx_fin); self.worker.start()
    def on_idx_fin(self, db):
        self.db = db; self.btn_refresh.setText("Re-Index"); self.progress.setVisible(False)
        with open(LIBRARY_INDEX_FILE, 'w') as f: json.dump(db, f)
    def on_search(self, t): self.current_query = t; self.search_timer.start(300)
    def perform_search(self):
        q = self.current_query.strip().lower()
        if not q: self.tr.setVisible(True); self.rl.setVisible(False); return
        self.tr.setVisible(False); self.rl.setVisible(True); self.rl.clear()
        for f, p, m in self.db:
            if all(x in f.lower() for x in q.split()): item = QListWidgetItem(f); item.setData(Qt.ItemDataRole.UserRole, p); self.rl.addItem(item)
    def toggle_focus(self):
        if self.is_focused: self.tr.setRootIndex(self.pm.mapFromSource(self.sm.index(self.root))); self.is_focused, self.btn_focus.setText = False, "Focus on Folder"
        else:
            idx = self.tr.currentIndex()
            if idx.isValid() and self.sm.isDir(self.pm.mapToSource(idx)): self.tr.setRootIndex(idx); self.is_focused, self.btn_focus.setText = True, "Show Full Library"
    def on_tree_click(self, idx): (not self.sm.isDir(self.pm.mapToSource(idx))) and self.file_selected.emit(self.sm.filePath(self.pm.mapToSource(idx)))
    def on_list_click(self, i): self.file_selected.emit(i.data(Qt.ItemDataRole.UserRole))
    def closeEvent(self, e): window_manager.record(self, "library_window"); super().closeEvent(e)

# --- 9. CART WALL ---
class CartWallWindow(QMainWindow):
    def __init__(self, ar):
        super().__init__(); self.setWindowTitle("Cart Wall"); self.resize(300, 700); self.setStyleSheet("background:#222;")
        self.ar = ar; window_manager.apply(self, "cart_window")
        self.last_cart_dir = USER_CONFIG_DIR
        cv = QWidget(); lay = QVBoxLayout(cv); lay.setContentsMargins(0,0,0,0)
        t_lay = QHBoxLayout(); self.lbl_tmr = QLabel("00:00"); self.lbl_tmr.setStyleSheet("background:#111; color:#ff5555; font-size:32px; font-weight:bold; font-family:monospace; padding:10px; border-bottom:2px solid #333;")
        self.lbl_clk = QLabel("00:00:00"); self.lbl_clk.setStyleSheet("background:#111; color:#00bcd4; font-size:32px; font-weight:bold; font-family:monospace; padding:10px; border-bottom:2px solid #333;")
        t_lay.addWidget(self.lbl_tmr); t_lay.addWidget(self.lbl_clk); lay.addLayout(t_lay)
        self.tabs = QTabWidget(); self.tabs.setStyleSheet("QTabBar::tab { background:#333; color:#888; padding:8px 20px; } QTabBar::tab:selected { background:#555; color:white; border-bottom:2px solid #00bcd4; }")
        lay.addWidget(self.tabs); self.setCentralWidget(cv); self.carts, self.active = [], []
        for n in ["Openers", "Donations", "General", "Misc"]:
            tab = QWidget(); g = QGridLayout(tab); g.setSpacing(6); g.setContentsMargins(4,4,4,4)
            for r in range(6):
                for c in range(3):
                    cart = CartButton(ar); cart.playing_triggered.connect(self.on_start); cart.finished_triggered.connect(self.on_fin)
                    g.addWidget(cart, r, c); self.carts.append(cart)
            self.tabs.addTab(tab, n)
        self.tmr = QTimer(); self.tmr.timeout.connect(self.upd_tmr); self.tmr.start(100)
        self.clk = QTimer(); self.clk.timeout.connect(lambda: self.lbl_clk.setText(QTime.currentTime().toString("HH:mm:ss"))); self.clk.start(500); self.current_file = None; self.load()
        menu = self.menuBar(); file_menu = menu.addMenu("File")
        for act, func in [("Save", self.save_current), ("Save As...", self.save_as), ("Open...", self.open_cartwall)]:
            a = QAction(act, self); a.triggered.connect(func); file_menu.addAction(a)
    def on_start(self, c): (c not in self.active) and self.active.append(c)
    def on_fin(self, c): (c in self.active) and self.active.remove(c)
    def upd_tmr(self):
        if not self.active: self.lbl_tmr.setText("00:00"); return
        ts = int(self.active[-1].get_rem_ns() / 1e9); self.lbl_tmr.setText(f"{ts//60:02}:{ts%60:02}")
    def load(self):
        if os.path.exists(CART_CONFIG_FILE):
            try:
                with open(CART_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.last_cart_dir = data.get("last_dir", USER_CONFIG_DIR)
                    lp = data.get("last_set")
                    if lp and os.path.exists(lp):
                        with open(lp, 'r') as j:
                            st = json.load(j)
                            for i, d in enumerate(st.get("carts", [])):
                                if i < len(self.carts) and d.get("path"): self.carts[i].load_file(d["path"], d.get("name"), d.get("color"), d.get("vol"))
                        self.current_file = lp
            except: pass
    def save_current(self): self.save_to_file(self.current_file) if self.current_file else self.save_as()
    def save_as(self):
        file, _ = QFileDialog.getSaveFileName(self, "Save Cartwall", self.last_cart_dir, "JSON (*.json)")
        if file:
            self.last_cart_dir = os.path.dirname(file)
            self.save_to_file(file); self.current_file = file; self.update_last_set(file)
    def open_cartwall(self):
        file, _ = QFileDialog.getOpenFileName(self, "Open Cartwall", self.last_cart_dir, "JSON (*.json)")
        if file:
            self.last_cart_dir = os.path.dirname(file)
            self.load_from_file(file); self.current_file = file; self.update_last_set(file)
    def save_to_file(self, file_path):
        data = {"carts": [cart.get_data() for cart in self.carts]}
        with open(file_path, 'w') as f: json.dump(data, f, indent=4)
    def load_from_file(self, file_path):
        try:
            with open(file_path, 'r') as f:
                st = json.load(f); [c.clear_cart() for c in self.carts]
                for i, d in enumerate(st.get("carts", [])):
                    if i < len(self.carts) and d.get("path"): self.carts[i].load_file(d["path"], d.get("name"), d.get("color"), d.get("vol"))
        except: pass
    def update_last_set(self, file_path):
        with open(CART_CONFIG_FILE, 'w') as f:
            json.dump({"last_set": file_path, "last_dir": self.last_cart_dir}, f, indent=4)
    def closeEvent(self, e): window_manager.record(self, "cart_window"); super().closeEvent(e)

# --- 10. MAIN WINDOW ---
class MainWindow(QWidget):
    def __init__(self, ar):
        super().__init__(); self.setWindowTitle("Broadcast Bar"); window_manager.apply(self, "player_window")
        self.ar, self.mm = ar, MidiMapper(); self.mw = MidiWorker(); self.mw.midi_signal.connect(self.mm.handle); self.mw.start()
        lay = QHBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0); self.players = [PlayerModule(i, self.mm, ar) for i in range(1,9)]
        for p in self.players: lay.addWidget(p)
    def load_auto(self, path): (next((p for p in self.players if not p.is_active()), self.players[0])).load_track(path)
    def closeEvent(self, e): window_manager.record(self, "player_window"); self.mw.stop(); super().closeEvent(e)

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setDesktopFileName("currycaster")
    ar = AudioRouter(); m = MainWindow(ar); m.show(); c = CartWallWindow(ar); c.show(); l = FileExplorerWindow(); l.show()
    l.file_selected.connect(m.load_auto); sys.exit(app.exec())
