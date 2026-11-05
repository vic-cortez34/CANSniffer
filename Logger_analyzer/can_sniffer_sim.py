import sys
import random
import time
from datetime import datetime, timezone
import csv
import re
import fnmatch
import json
import threading
from pathlib import Path
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple
from bisect import bisect_left

try:
    import openai
except ImportError:
    openai = None

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSpinBox,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QGroupBox,
    QLineEdit,
    QGridLayout,
    QSpinBox as QtSpinBox,
    QMessageBox,
    QTextEdit,
    QSplitter,
    QScrollArea,
    QFrame,
    QInputDialog,
    QDoubleSpinBox,
    QSlider,
)

import pyqtgraph as pg

# Configuration - Set your OpenAI API key here
OPENAI_API_KEY = "OPEN_AI_API"  # Replace with your actual API key


class CANReverseEngineeringAgent:
    """GPT-powered agent specialized in CAN bus reverse engineering for OBDII and J1939."""
    
    def __init__(self):
        self.api_key = None
        self.client = None
        self.conversation_history = []
        
        # Specialized knowledge base
        self.obdii_pids = {
            0x01: "Show current data",
            0x02: "Show freeze frame data", 
            0x03: "Show stored DTCs",
            0x04: "Clear DTCs",
            0x05: "Test results, oxygen sensor monitoring",
            0x06: "Test results, other component/system monitoring",
            0x07: "Show pending DTCs",
            0x08: "Control operation of on-board component/system",
            0x09: "Request vehicle information",
            0x0A: "Permanent DTCs"
        }
        
        self.j1939_pgns = {
            0xF004: "Electronic Engine Controller 1 (EEC1)",
            0xF003: "Electronic Engine Controller 2 (EEC2)", 
            0xF002: "Electronic Engine Controller 3 (EEC3)",
            0xFEF1: "Cruise Control/Vehicle Speed",
            0xFEF2: "Fuel Economy",
            0xFEE9: "Vehicle Position",
            0xFECA: "Vehicle Direction/Speed",
            0xFEC1: "High Resolution Vehicle Distance",
            0xF010: "Aftertreatment 1 Intake Gas 1",
            0xF011: "Aftertreatment 1 Exhaust Gas 1"
        }
    
    def set_api_key(self, api_key: str):
        """Set OpenAI API key and initialize client."""
        self.api_key = api_key
        if openai and api_key:
            try:
                self.client = openai.OpenAI(api_key=api_key)
                return True
            except Exception as e:
                print(f"Failed to initialize OpenAI client: {e}")
                return False
        return False
    
    def analyze_frame(self, frame: 'CANFrame') -> str:
        """Analyze a single CAN frame and provide insights."""
        can_id = frame.can_id
        data = frame.data
        
        # Protocol detection
        protocol_info = self._detect_protocol(can_id)
        
        # Basic analysis
        analysis = f"CAN ID: 0x{can_id:X}\n"
        analysis += f"Data: {data.hex().upper()}\n"
        analysis += f"Length: {len(data)} bytes\n"
        analysis += f"Protocol: {protocol_info}\n"
        
        # Specialized analysis based on protocol
        if protocol_info.startswith("OBDII"):
            analysis += self._analyze_obdii(data)
        elif protocol_info.startswith("J1939"):
            analysis += self._analyze_j1939(can_id, data)
        
        return analysis
    
    def _detect_protocol(self, can_id: int) -> str:
        """Detect the likely protocol based on CAN ID."""
        if 0x7E0 <= can_id <= 0x7E7:
            return "OBDII Request"
        elif 0x7E8 <= can_id <= 0x7EF:
            return "OBDII Response"
        elif can_id & 0x1FFFF000 == 0x18000000:  # J1939 format
            pgn = (can_id >> 8) & 0xFFFF
            if pgn in self.j1939_pgns:
                return f"J1939 - {self.j1939_pgns[pgn]}"
            return f"J1939 - PGN 0x{pgn:04X}"
        elif can_id > 0x7FF:  # Extended frame
            return "Extended CAN (possibly J1939)"
        else:
            return "Standard CAN"
    
    def _analyze_obdii(self, data: bytes) -> str:
        """Analyze OBDII frame data."""
        if len(data) < 2:
            return "Insufficient data for OBDII analysis\n"
        
        analysis = ""
        mode = data[1] if len(data) > 1 else 0
        
        if mode in self.obdii_pids:
            analysis += f"OBDII Mode: 0x{mode:02X} - {self.obdii_pids[mode]}\n"
        
        if mode == 0x01 and len(data) >= 3:  # Current data
            pid = data[2]
            analysis += f"PID: 0x{pid:02X}\n"
            if pid == 0x0C and len(data) >= 5:  # Engine RPM
                rpm = ((data[3] << 8) + data[4]) / 4
                analysis += f"Engine RPM: {rpm}\n"
            elif pid == 0x0D and len(data) >= 4:  # Vehicle speed
                speed = data[3]
                analysis += f"Vehicle Speed: {speed} km/h\n"
        
        return analysis
    
    def _analyze_j1939(self, can_id: int, data: bytes) -> str:
        """Analyze J1939 frame data."""
        pgn = (can_id >> 8) & 0xFFFF
        sa = can_id & 0xFF  # Source Address
        
        analysis = f"Source Address: 0x{sa:02X}\n"
        analysis += f"PGN: 0x{pgn:04X}\n"
        
        if pgn == 0xF004:  # EEC1
            if len(data) >= 4:
                torque = data[1] - 125  # % of reference torque
                speed_lsb = data[3]
                speed_msb = data[4] if len(data) > 4 else 0
                rpm = ((speed_msb << 8) + speed_lsb) * 0.125
                analysis += f"Engine Torque: {torque}%\n"
                analysis += f"Engine Speed: {rpm} RPM\n"
        
        return analysis
    
    async def chat(self, message: str, context_frames: List['CANFrame'] = None) -> str:
        """Chat with the GPT agent about CAN bus reverse engineering."""
        if not self.client:
            return "GPT agent not configured. Please set your OpenAI API key."
        
        # Build context from recent frames
        context = ""
        if context_frames:
            context = "Recent CAN frames:\n"
            for frame in context_frames[-10:]:  # Last 10 frames
                context += f"ID: 0x{frame.can_id:X}, Data: {frame.data.hex().upper()}\n"
            context += "\n"
        
        # System prompt for CAN bus reverse engineering
        system_prompt = """You are a specialized CAN bus reverse engineering expert with deep knowledge of:
        - OBDII protocols and PIDs
        - J1939 protocols and PGNs  
        - Automotive ECU communication patterns
        - CAN frame analysis and interpretation
        - Common automotive data formats and scaling
        
        Help users analyze CAN bus data, identify protocols, decode messages, and understand ECU behavior.
        Be concise but thorough in your explanations."""
        
        try:
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history (last 5 exchanges)
            messages.extend(self.conversation_history[-10:])
            
            # Add current context and message
            user_message = context + message
            messages.append({"role": "user", "content": user_message})
            
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=messages,
                max_tokens=500,
                temperature=0.7
            )
            
            assistant_response = response.choices[0].message.content
            
            # Update conversation history
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": assistant_response})
            
            # Keep only last 20 messages to manage context length
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]
            
            return assistant_response
            
        except Exception as e:
            return f"Error communicating with GPT: {str(e)}"


@dataclass
class CANFrame:
    ts: float  # seconds since start
    can_id: int
    data: bytes  # length 0..8
    orig_abs_str: str | None = None  # original CSV absolute timestamp string (9th column), if available


class CANSimulator:
    """Generate CAN frames in pseudo real-time with controllable frequencies per ID.

    Implementation: every tick of size dt, for each configured ID with rate r (Hz)
    it emits with probability p = r * dt (Bernoulli approximation of Poisson).
    """

    def __init__(self, dt_ms: int = 50):
        self.dt = dt_ms / 1000.0
        # Define some IDs and their base rates (Hz) to simulate high/low frequency traffic
        self.id_rates: Dict[int, float] = {
            0x07E0: 20.0,  # high frequency
            0x07E8: 10.0,
            0x100: 5.0,
            0x101: 2.0,
            0x200: 1.0,
            0x300: 0.5,
            0x400: 0.2,
        }
        # Keep last data per ID to sometimes make small changes instead of random
        self.last_data: Dict[int, bytearray] = {}
        self.start_time = time.perf_counter()

    def tick(self) -> List[CANFrame]:
        frames: List[CANFrame] = []
        now = time.perf_counter() - self.start_time
        for can_id, rate in self.id_rates.items():
            p = rate * self.dt
            if random.random() < p:
                # Generate data: start from previous and tweak 0-2 random bytes
                prev = self.last_data.get(can_id)
                if prev is None:
                    buf = bytearray(random.getrandbits(8) for _ in range(8))
                else:
                    buf = bytearray(prev)
                    for _ in range(random.randint(0, 2)):
                        idx = random.randrange(8)
                        # +/- small change
                        delta = random.choice([-3, -2, -1, 1, 2, 3])
                        buf[idx] = (buf[idx] + delta) & 0xFF
                self.last_data[can_id] = buf
                frames.append(CANFrame(ts=now, can_id=can_id, data=bytes(buf)))
        return frames


class CSVPlayer:
    """Load and replay CAN frames from a CSV with columns similar to SavvyCAN.

    Expected columns (robust to naming):
      - start_time: float seconds (used for timing). If missing, timestamps will be sequential.
      - id: hex string (e.g., 0x...07E0) or plain hex like 7E0. If missing, try parsing from 'datastring'.
      - data: hex bytes as a single hex string (e.g., 0x02010C0000000000) or plain hex without 0x.
      - datastring: e.g., "7E0#02.01.0C.00.00.00.00.00" or similar; we parse ID and bytes.

    The loader normalizes timestamps to start at t=0.
    """

    BYTE_SPLIT = re.compile(r"[\s\.:,_-]+")

    def __init__(self):
        self.frames: List[CANFrame] = []
        self.duration: float = 0.0
        self.base_epoch: float | None = None  # absolute epoch seconds from CSV if provided
        self.is_epoch: bool = False  # True if CSV timestamps appear to be absolute epoch seconds
        self.base_rel_start: float | None = None  # first CSV timestamp when not epoch
        self.file_path: str | None = None  # track last loaded CSV path for context

    @staticmethod
    def _parse_hex_int(text: str) -> int:
        t = text.strip()
        if t.lower().startswith("0x"):
            return int(t, 16)
        return int(t, 16)

    @staticmethod
    def _parse_bytes_from_hex(text: str) -> bytes:
        t = text.strip()
        if t.lower().startswith("0x"):
            t = t[2:]
        # pad if odd
        if len(t) % 2 == 1:
            t = "0" + t
        raw = bytes.fromhex(t)
        # Ensure length 8
        if len(raw) < 8:
            raw = raw + bytes(8 - len(raw))
        return raw[:8]

    @classmethod
    def _parse_from_datastring(cls, s: str) -> Tuple[int | None, bytes | None]:
        if not s:
            return None, None
        part = s.strip()
        # Common formats: "7E0#02.01.0C.00.00.00.00.00" or "7E0 02 01 0C ..."
        # Split on separators
        pieces = cls.BYTE_SPLIT.split(part)
        pieces = [p for p in pieces if p]
        if not pieces:
            return None, None
        # First piece could be ID (hex). Validate length <= 8 and hex
        try:
            can_id = int(pieces[0], 16)
            data_hex = pieces[1:1+8]
        except Exception:
            return None, None
        # Convert bytes
        data: List[int] = []
        for p in data_hex:
            try:
                data.append(int(p, 16) & 0xFF)
            except Exception:
                break
        while len(data) < 8:
            data.append(0)
        return can_id, bytes(data[:8])

    def load(self, file_path: str) -> None:
        self.frames.clear()
        self.file_path = file_path
        times: List[float] = []
        got_any_ts = False
        first_ts_val: float | None = None
        with open(file_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Normalize field names to lowercase for resilience
            field_map = {k.lower(): k for k in reader.fieldnames or []}
            # Determine 9th column header if available (authoritative timestamp per user)
            ninth_header = None
            if reader.fieldnames and len(reader.fieldnames) >= 9:
                ninth_header = reader.fieldnames[8]
            for row in reader:
                # Scheduling timestamp from start_time (seconds since sampling start)
                ts = None
                orig_abs_str: str | None = None
                # Capture absolute 9th-column text if present
                if ninth_header and ninth_header in row:
                    v9 = (row.get(ninth_header) or "").strip()
                    if v9:
                        orig_abs_str = v9
                        # Also parse to seconds for epoch heuristic
                        try:
                            dt = datetime.strptime(v9, "%Y-%m-%d %H:%M:%S")
                            abs_sec = dt.timestamp()
                            if first_ts_val is None:
                                first_ts_val = abs_sec
                                got_any_ts = True
                        except Exception:
                            try:
                                s = re.sub(r"\s+", " ", v9)
                                s = re.sub(r"(?i)\b(a\.?\s*m\.?)\b", "AM", s)
                                s = re.sub(r"(?i)\b(p\.?\s*m\.?)\b", "PM", s)
                                dt = datetime.strptime(s, "%d/%m/%Y %I:%M:%S %p")
                                abs_sec = dt.timestamp()
                                if first_ts_val is None:
                                    first_ts_val = abs_sec
                                    got_any_ts = True
                            except Exception:
                                pass
                # Use start_time for playback scheduling
                st_key = None
                for k in ("start_time", "start time"):
                    if k in field_map:
                        st_key = field_map[k]
                        break
                if st_key:
                    v = (row.get(st_key) or "").strip()
                    if v:
                        try:
                            ts = float(v)
                        except Exception:
                            ts = None
                # Fallbacks if start_time missing
                if ts is None:
                    # Try numeric seconds-like fields
                    for key in ("time", "timestamp_s", "time_s"):
                        if key in field_map:
                            v = row[field_map[key]].strip()
                            if v:
                                try:
                                    ts = float(v)
                                except Exception:
                                    ts = None
                            if ts is not None:
                                break
                # ID
                can_id = None
                if "id" in field_map:
                    v = row[field_map["id"]].strip()
                    if v:
                        try:
                            # Could be 0x... or decimal or hex
                            if v.lower().startswith("0x"):
                                can_id = int(v, 16)
                            else:
                                # Some exports include long 0-padded 64-bit hex, try int(v,16)
                                can_id = int(v, 16)
                        except Exception:
                            can_id = None

                data_bytes: bytes | None = None
                # Prefer explicit data column if present
                if "data" in field_map:
                    dv = row[field_map["data"]].strip()
                    # If data column exists but is empty or '0x', skip this row entirely to avoid confusion
                    if not dv or dv.lower() == "0x":
                        continue
                    try:
                        data_bytes = self._parse_bytes_from_hex(dv)
                    except Exception:
                        data_bytes = None

                # Parse datastring as fallback or to get ID if missing (only when 'data' column isn't present)
                if ("data" not in field_map) and (can_id is None or data_bytes is None) and "datastring" in field_map:
                    ds = row[field_map["datastring"]]
                    ds_id, ds_data = self._parse_from_datastring(ds)
                    if can_id is None:
                        can_id = ds_id
                    if data_bytes is None:
                        data_bytes = ds_data

                if ts is None:
                    # If missing, append sequential increments of 0.01s
                    ts = times[-1] + 0.01 if times else 0.0

                if can_id is None or data_bytes is None:
                    continue  # skip malformed row

                times.append(ts)
                # Keep full CAN ID (supports 29-bit identifiers)
                self.frames.append(CANFrame(ts=ts, can_id=can_id, data=data_bytes, orig_abs_str=orig_abs_str))

        # Normalize timestamps to start at 0 (relative seconds based on start_time)
        if times:
            t0 = times[0]
            for i, fr in enumerate(self.frames):
                self.frames[i] = CANFrame(ts=fr.ts - t0, can_id=fr.can_id, data=fr.data, orig_abs_str=fr.orig_abs_str)
            self.duration = times[-1] - t0
            # Determine if CSV provided absolute epoch seconds (rough heuristic)
            # Anything greater than ~1973-03-03 (1e8 seconds since epoch) treated as epoch
            self.is_epoch = bool(got_any_ts and first_ts_val is not None and first_ts_val > 1e8)
            # Preserve CSV absolute base epoch if detected as epoch
            self.base_epoch = first_ts_val if self.is_epoch else None
            # For non-epoch CSV, keep the original first timestamp to display exact values
            self.base_rel_start = None if self.is_epoch else (first_ts_val or 0.0)
        else:
            self.duration = 0.0
            self.base_epoch = None
            self.is_epoch = False
            self.base_rel_start = None


class SnifferWindow(QWidget):
    COL_TIMESTAMP = 0
    COL_FREQ = 1
    COL_COUNT = 2
    COL_ID = 3
    COL_DATA_START = 4  # up to 11

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN Sniffer Simulator (SavvyCAN-like)")
        self.resize(1100, 600)

        # State
        self.sim = CANSimulator(dt_ms=50)
        self.csv_player = CSVPlayer()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.on_tick)
        self.running = False
        self.mode = "Latched"  # default to Latched
        self.source = "CSV"  # default to CSV
        self.play_start_monotonic = None  # type: float | None
        # Base epoch (seconds since Unix epoch) to map relative frame times to wall-clock
        self.display_epoch_base = time.time()
        # Runtime verification: log first 3 CSV timestamps vs table/clock
        self._csv_verify_count = 0

        # Frequency tracking (sliding window)
        self.window_sec = 2.0
        self.id_times: Dict[int, Deque[float]] = defaultdict(deque)

        # Latched tracking
        self.id_row: Dict[int, int] = {}
        self.id_last_data: Dict[int, bytes] = {}
        self.id_prev_data: Dict[int, bytes] = {}
        self.id_counts: Dict[int, int] = {}

        # Graphing state
        self.graph_enabled = False
        self.graph_cfg = None  # type: GraphConfig | None
        self.graph_x = deque(maxlen=5000)
        self.graph_y = deque(maxlen=5000)

        # GPT Agent for reverse engineering assistance
        self.gpt_agent = CANReverseEngineeringAgent()
        self.recent_frames = deque(maxlen=50)  # Keep recent frames for context

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Controls (split into two rows for cleaner look)
        ctl1 = QHBoxLayout()
        ctl2 = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")
        self.btn_reset = QPushButton("Reset")
        self.btn_clear = QPushButton("Clear Table")
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Continuous", "Latched"])
        self.cmb_mode.currentTextChanged.connect(self.on_mode_changed)

        self.cmb_source = QComboBox()
        self.cmb_source.addItems(["Simulation", "CSV"])
        self.cmb_source.currentTextChanged.connect(self.on_source_changed)
        self.btn_load_csv = QPushButton("Load CSV…")
        self.btn_load_csv.clicked.connect(self.load_csv)
        self.chk_loop = QCheckBox("Loop")

        self.chk_filter = QCheckBox("Ignore IDs above freq")
        self.spn_freq = QSpinBox()
        self.spn_freq.setRange(1, 1000)
        self.spn_freq.setValue(15)  # default threshold
        self.lbl_hz = QLabel("Hz")

        # ID Filter controls
        self.chk_id_filter = QCheckBox("ID Filter")
        self.txt_id_filter = QLineEdit()
        self.txt_id_filter.setPlaceholderText("IDs: 7E0,7E8,0xCFE6C00 or patterns: CFE*, 0xCF??400")
        self.txt_id_filter.setToolTip(
            "Enter comma-separated hex IDs or patterns. Examples:\n"
            "- Exact: 7E0, 0xCFE6C00\n"
            "- Prefix: CFE* (matches CFExxxx)\n"
            "- Wildcards: 0xCF??400 (?: any hex, *: any length)\n"
            "Case-insensitive; 0x prefix optional."
        )
        self.chk_id_filter.setToolTip("Enable hex ID filter. Supports exact IDs and wildcards.")

        # Running clock
        self.lbl_clock_title = QLabel("Clock:")
        self.lbl_clock = QLabel("")

        # Timestamp format selector
        self.lbl_tsfmt = QLabel("Timestamp:")
        self.cmb_ts_format = QComboBox()
        self.cmb_ts_format.addItems([
            "Auto",              # CSV epoch -> Absolute; CSV relative -> Rel seconds
            "Absolute",          # Always absolute date-time using display_epoch_base
            "Rel seconds",       # seconds.mmm (uses CSV base if available)
            "Rel HH:MM:SS.mmm",  # formatted relative time
        ])
        self.cmb_ts_format.currentTextChanged.connect(self.on_ts_format_changed)

        # View toggles
        self.chk_show_bits = QCheckBox("Show Bit View")
        self.chk_show_bits.setChecked(False)
        self.chk_show_graph = QCheckBox("Show Graph")
        self.chk_show_graph.setChecked(False)
        self.chk_show_assistant = QCheckBox("Show Assistant")
        self.chk_show_assistant.setChecked(False)

        # Row 1: Mode/Source/CSV/Loop + Timestamp + Clock + main actions
        ctl1.addWidget(QLabel("Mode:"))
        ctl1.addWidget(self.cmb_mode)
        ctl1.addSpacing(12)
        ctl1.addWidget(QLabel("Source:"))
        ctl1.addWidget(self.cmb_source)
        ctl1.addWidget(self.btn_load_csv)
        ctl1.addWidget(self.chk_loop)
        # Seek controls
        ctl1.addSpacing(12)
        ctl1.addWidget(QLabel("Seek (s):"))
        self.spn_seek = QDoubleSpinBox()
        self.spn_seek.setDecimals(3)
        self.spn_seek.setRange(0.0, 0.0)
        self.spn_seek.setSingleStep(0.1)
        self.spn_seek.setEnabled(False)
        ctl1.addWidget(self.spn_seek)
        self.btn_seek = QPushButton("Go")
        self.btn_seek.setEnabled(False)
        ctl1.addWidget(self.btn_seek)
        # Timeline slider
        ctl1.addSpacing(12)
        ctl1.addWidget(QLabel("Timeline:"))
        self.sld_timeline = QSlider(Qt.Horizontal)
        self.sld_timeline.setEnabled(False)
        self.sld_timeline.setMinimum(0)
        self.sld_timeline.setMaximum(0)
        self.sld_timeline.setSingleStep(100)  # 100 ms step
        self.sld_timeline.setPageStep(1000)   # 1 s page
        ctl1.addWidget(self.sld_timeline)
        self.lbl_timeline_pos = QLabel("0.000s")
        ctl1.addWidget(self.lbl_timeline_pos)
        ctl1.addSpacing(12)
        ctl1.addWidget(self.lbl_tsfmt)
        ctl1.addWidget(self.cmb_ts_format)
        ctl1.addSpacing(8)
        ctl1.addWidget(self.lbl_clock_title)
        ctl1.addWidget(self.lbl_clock)
        ctl1.addStretch(1)
        ctl1.addWidget(self.btn_clear)
        ctl1.addWidget(self.btn_reset)
        ctl1.addWidget(self.btn_start)
        ctl1.addWidget(self.btn_stop)

        # Row 2: Filters and view toggles
        ctl2.addWidget(self.chk_filter)
        ctl2.addWidget(self.spn_freq)
        ctl2.addWidget(self.lbl_hz)
        ctl2.addSpacing(12)
        ctl2.addWidget(self.chk_id_filter)
        ctl2.addWidget(self.txt_id_filter)
        ctl2.addStretch(1)
        ctl2.addWidget(self.chk_show_bits)
        ctl2.addWidget(self.chk_show_graph)
        ctl2.addWidget(self.chk_show_assistant)
        # Help button
        self.btn_help = QPushButton("Help")
        self.btn_help.setToolTip("Show usage and feature help")
        ctl2.addWidget(self.btn_help)
        
        # GPT Agent button
        self.btn_gpt_config = QPushButton("GPT Config")
        self.btn_gpt_config.setToolTip("Configure OpenAI API key for CAN analysis")
        ctl2.addWidget(self.btn_gpt_config)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_clear.clicked.connect(self.clear_table)
        self.btn_reset.clicked.connect(self.reset_playback)
        self.btn_help.clicked.connect(self.show_help)
        self.btn_gpt_config.clicked.connect(self.configure_gpt)
        # Seek action
        try:
            self.btn_seek.clicked.connect(self.on_seek_clicked)
        except Exception:
            pass
        # Timeline actions
        try:
            self.sld_timeline.sliderReleased.connect(self.on_timeline_released)
            self.sld_timeline.valueChanged.connect(self.on_timeline_moved)
        except Exception:
            pass

        # ID filter parsing
        self._id_filter_set: set[int] = set()
        self._id_filter_patterns: list[str] = []  # wildcard/prefix patterns over hex ID
        self.txt_id_filter.textChanged.connect(self._parse_id_filter_text)
        
        layout.addLayout(ctl1)
        layout.addLayout(ctl2)

        # Connections for view toggles
        self.chk_show_bits.toggled.connect(self.on_toggle_bits)
        self.chk_show_graph.toggled.connect(self.on_toggle_graph)
        self.chk_show_assistant.toggled.connect(self.on_toggle_assistant)

        # ... (rest of the code remains the same)
        # Main content area with splitter for table and chat
        main_splitter = QSplitter(Qt.Horizontal)
        
        # Left side: Frame Table
        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        
        self.table = QTableWidget(0, 12)
        headers = [
            "timestamp",
            "frequency",
            "count",
            "id",
            "b0",
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "b6",
            "b7",
        ]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        table_layout.addWidget(self.table)
        
        main_splitter.addWidget(table_widget)
        
        # Right side: GPT Chat Panel
        self.chat_panel = QGroupBox("CAN Reverse Engineering Assistant")
        chat_layout = QVBoxLayout(self.chat_panel)
        
        # Chat history
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setMaximumHeight(300)
        self.chat_history.append("Welcome to the CAN Reverse Engineering Assistant!\n"
                                "Configure your OpenAI API key to start chatting about CAN bus analysis.\n"
                                "I can help with OBDII, J1939, and general CAN frame analysis.")
        chat_layout.addWidget(self.chat_history)
        
        # Quick analysis buttons
        quick_buttons = QHBoxLayout()
        self.btn_analyze_selected = QPushButton("Analyze Selected Frame")
        self.btn_analyze_selected.setToolTip("Get AI analysis of the currently selected frame")
        self.btn_protocol_detect = QPushButton("Detect Protocols")
        self.btn_protocol_detect.setToolTip("Analyze recent frames to detect protocols")
        self.btn_analyze_csv = QPushButton("Analyze CSV")
        self.btn_analyze_csv.setToolTip("Summarize the loaded CSV and ask GPT for analysis")
        quick_buttons.addWidget(self.btn_analyze_selected)
        quick_buttons.addWidget(self.btn_protocol_detect)
        quick_buttons.addWidget(self.btn_analyze_csv)
        quick_buttons.addStretch()
        chat_layout.addLayout(quick_buttons)
        
        # Chat input
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask about CAN frames, protocols, or reverse engineering...")
        self.btn_send = QPushButton("Send")
        self.btn_send.setEnabled(False)  # Disabled until API key is set
        self.btn_analyze_csv.setEnabled(False)  # enable after GPT key and CSV loaded
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.btn_send)
        chat_layout.addLayout(input_layout)
        
        # Connect chat events
        self.btn_send.clicked.connect(self.send_chat_message)
        self.chat_input.returnPressed.connect(self.send_chat_message)
        self.btn_analyze_selected.clicked.connect(self.analyze_selected_frame)
        self.btn_protocol_detect.clicked.connect(self.detect_protocols)
        self.btn_analyze_csv.clicked.connect(self.analyze_csv)
        
        main_splitter.addWidget(self.chat_panel)
        main_splitter.setSizes([700, 400])  # Give more space to table initially
        
        layout.addWidget(main_splitter)

        # Bit View Group
        self.bit_grp = QGroupBox("Bit View (selected row)")
        bit_layout = QVBoxLayout(self.bit_grp)
        self.bit_table = QTableWidget(8, 8)
        self.bit_table.setHorizontalHeaderLabels(["b7","b6","b5","b4","b3","b2","b1","b0"])
        self.bit_table.setVerticalHeaderLabels([f"byte{i}" for i in range(8)])
        self.bit_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bit_table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bit_table.setEditTriggers(QTableWidget.NoEditTriggers)
        bit_layout.addWidget(self.bit_table)
        layout.addWidget(self.bit_grp)

        # Graph Settings and Plot
        graph_container = QHBoxLayout()

        settings_grp = QGroupBox("Graph Settings")
        s_layout = QVBoxLayout(settings_grp)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Name:"))
        self.txt_sig_name = QLineEdit()
        self.txt_sig_name.setPlaceholderText("Signal name")
        row1.addWidget(self.txt_sig_name)
        s_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("ID:"))
        self.txt_sig_id = QLineEdit("0x7E0")
        row2.addWidget(self.txt_sig_id)
        s_layout.addLayout(row2)

        s_layout.addWidget(QLabel("Start Bit:"))
        self.grid_bits = QTableWidget(8, 8)
        # Label like image: numbers 63..0 with columns b7..b0 across top
        self.grid_bits.setHorizontalHeaderLabels(["7","6","5","4","3","2","1","0"])
        self.grid_bits.setVerticalHeaderLabels([str(r) for r in range(8)])
        self.grid_bits.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid_bits.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid_bits.setEditTriggers(QTableWidget.NoEditTriggers)
        # Fill numbers with canonical mapping:
        # row = byte index (0..7), column = bit label (b7..b0),
        # and the numeric label equals LE bit index = row*8 + (7 - column)
        for r in range(8):
            for c in range(8):
                le_index = r * 8 + (7 - c)
                item = QTableWidgetItem(str(le_index))
                item.setTextAlignment(Qt.AlignCenter)
                self.grid_bits.setItem(r, c, item)
        self.grid_bits.cellClicked.connect(self.on_grid_bit_clicked)
        s_layout.addWidget(self.grid_bits)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Data Len (bits):"))
        self.spn_len_bits = QtSpinBox()
        self.spn_len_bits.setRange(1, 64)
        self.spn_len_bits.setValue(8)
        row3.addWidget(self.spn_len_bits)
        s_layout.addLayout(row3)

        row4 = QHBoxLayout()
        self.chk_little = QCheckBox("Little Endian (LSB first)")
        row4.addWidget(self.chk_little)
        self.chk_signed = QCheckBox("Signed")
        row4.addWidget(self.chk_signed)
        s_layout.addLayout(row4)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Scale:"))
        self.txt_scale = QLineEdit("1.0")
        row5.addWidget(self.txt_scale)
        row5.addWidget(QLabel("Bias:"))
        self.txt_bias = QLineEdit("0.0")
        row5.addWidget(self.txt_bias)
        s_layout.addLayout(row5)

        btn_row = QHBoxLayout()
        self.btn_apply_graph = QPushButton("Apply Graph Config")
        self.btn_clear_graph = QPushButton("Clear Graph")
        btn_row.addWidget(self.btn_apply_graph)
        btn_row.addWidget(self.btn_clear_graph)
        s_layout.addLayout(btn_row)

        self.btn_apply_graph.clicked.connect(self.apply_graph_config)
        self.btn_clear_graph.clicked.connect(self.clear_graph)

        graph_container.addWidget(settings_grp)

        plot_grp = QGroupBox("Real-time Signal Plot")
        p_layout = QVBoxLayout(plot_grp)
        self.plot = pg.PlotWidget()
        self.plot.setBackground('w')
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel('bottom', 'Time', units='s')
        self.plot.setLabel('left', 'Value')
        self.plot_curve = self.plot.plot(pen=pg.mkPen(color=(0, 100, 200), width=2))
        p_layout.addWidget(self.plot)
        graph_container.addWidget(plot_grp, stretch=1)

        # Wrap graph container for show/hide
        self.graph_panel = QWidget()
        self.graph_panel.setLayout(graph_container)
        layout.addWidget(self.graph_panel)

        self.setLayout(layout)

        # Apply default selections for Mode and Source
        try:
            self.cmb_mode.setCurrentText("Latched")
            self.cmb_source.setCurrentText("CSV")
        except Exception:
            pass

        # Ensure initial visibility matches default unchecked state
        try:
            self.on_toggle_bits(self.chk_show_bits.isChecked())
            self.on_toggle_graph(self.chk_show_graph.isChecked())
            self.on_toggle_assistant(self.chk_show_assistant.isChecked())
        except Exception:
            pass

    def on_seek_clicked(self):
        try:
            t = float(self.spn_seek.value())
        except Exception:
            return
        self.seek_to_time(t)

    def on_timeline_moved(self, value: int):
        # Update preview label while sliding (value in ms)
        sec = max(0.0, value / 1000.0)
        self.lbl_timeline_pos.setText(f"{sec:.3f}s")

    def on_timeline_released(self):
        # Seek when user releases the slider thumb
        val = int(self.sld_timeline.value())
        sec = max(0.0, val / 1000.0)
        self.seek_to_time(sec)

    def seek_to_time(self, seconds: float):
        """Jump playback to a specific relative timestamp in the currently loaded CSV.
        Clears the table and reinitializes playback at the target time.
        """
        if self.source != "CSV" or not self.csv_player.frames:
            return
        # Clamp target
        duration = max(0.0, float(self.csv_player.duration))
        t = min(max(0.0, float(seconds)), duration)
        frs = self.csv_player.frames
        # Build list of timestamps for bisect
        ts_list = [fr.ts for fr in frs]
        idx = bisect_left(ts_list, t)
        # Reset table/state
        self.clear_table()
        self.id_times.clear()
        self.id_row.clear()
        self.id_last_data.clear()
        self.id_prev_data.clear()
        self.id_counts.clear()
        # Set playback index and wall clock alignment
        self._csv_play_index = idx
        self.play_start_monotonic = time.perf_counter() - t
        # Update display epoch base consistent with start()
        if self.csv_player.is_epoch and self.csv_player.base_epoch is not None:
            self.display_epoch_base = self.csv_player.base_epoch
        else:
            self.display_epoch_base = time.time()

    def on_toggle_assistant(self, checked: bool):
        # Show/hide the GPT assistant panel
        try:
            self.chat_panel.setVisible(checked)
        except Exception:
            pass

    def on_grid_bit_clicked(self, r: int, c: int):
        # Map r,c to bit index label
        item = self.grid_bits.item(r, c)
        if item:
            # Just visually select cell; the value is read during apply
            pass

    def on_toggle_bits(self, checked: bool):
        self.bit_grp.setVisible(checked)

    def on_toggle_graph(self, checked: bool):
        self.graph_panel.setVisible(checked)

    def show_help(self):
        msg = (
            "CAN Sniffer Simulator Help\n\n"
            "Playback & Sources:\n"
            "- Source: choose Simulation or CSV; Load CSV to import frames.\n"
            "- Start/Stop/Reset control playback; Loop repeats CSV when done.\n\n"
            "Timestamps:\n"
            "- Timestamp selector controls clock display (Auto/Absolute/Relative).\n"
            "- Table shows relative seconds based on CSV start_time.\n\n"
            "Filtering:\n"
            "- Ignore IDs above freq: hides IDs with measured frequency above threshold.\n"
            "- ID Filter: comma-separated hex IDs or patterns. Examples:\n"
            "  * Exact: 7E0, 0xCFE6C00\n"
            "  * Prefix: CFE*  (matches CFExxxx)\n"
            "  * Wildcards: 0xCF??400 (?: single hex, *: any length)\n"
            "  * Case-insensitive; 0x optional.\n\n"
            "Views:\n"
            "- Show Bit View: displays bits of the selected row's bytes and highlights changes.\n"
            "- Show Graph: configure a signal (ID, start bit, length, scaling) and plot over time.\n\n"
            "Notes:\n"
            "- Supports full 29-bit CAN IDs; IDs display as 0xHEX without leading zeros.\n"
            "- The clock shows the absolute timestamp from CSV (9th column) if present.\n"
        )
        try:
            QMessageBox.information(self, "Help", msg)
        except Exception:
            # Fallback: print to console if QMessageBox fails
            print(msg)
    
    def configure_gpt(self):
        """Configure OpenAI API key for GPT agent using hardcoded key."""
        if not openai:
            QMessageBox.warning(self, "OpenAI Not Available", 
                              "OpenAI library not installed. Install with: pip install openai>=1.0.0")
            return
        
        if OPENAI_API_KEY == "your-api-key-here":
            QMessageBox.warning(self, "API Key Not Set", 
                              "Please set your OpenAI API key in the OPENAI_API_KEY constant at the top of the file.")
            return
        
        if self.gpt_agent.set_api_key(OPENAI_API_KEY):
            self.btn_send.setEnabled(True)
            self.btn_analyze_selected.setEnabled(True)
            self.btn_protocol_detect.setEnabled(True)
            # Enable CSV analysis if a CSV is loaded
            self.btn_analyze_csv.setEnabled(bool(self.csv_player.frames))
            self.chat_history.append("\n✅ GPT Agent configured successfully! You can now chat about CAN analysis.")
            self.btn_gpt_config.setText("GPT ✓")
            self.btn_gpt_config.setStyleSheet("background-color: #90EE90;")
        else:
            QMessageBox.warning(self, "Configuration Failed", 
                              "Failed to configure GPT agent. Check your API key in the code.")
    
    def send_chat_message(self):
        """Send a message to the GPT agent."""
        message = self.chat_input.text().strip()
        if not message:
            return
        
        self.chat_input.clear()
        self.chat_history.append(f"\n🧑 You: {message}")
        self.chat_history.append("🤖 Assistant: Thinking...")
        
        # Run GPT request in a separate thread to avoid blocking UI
        def chat_worker():
            try:
                # Convert recent_frames to list for the async call
                context_frames = list(self.recent_frames) if self.recent_frames else None
                
                # Since we can't use async in PyQt easily, we'll make a synchronous version
                response = self._sync_chat(message, context_frames)
                
                # Update UI in main thread
                self.chat_history.textCursor().movePosition(self.chat_history.textCursor().End)
                cursor = self.chat_history.textCursor()
                cursor.movePosition(cursor.End)
                cursor.movePosition(cursor.StartOfLine, cursor.KeepAnchor)
                cursor.removeSelectedText()
                self.chat_history.append(f"🤖 Assistant: {response}")
                
            except Exception as e:
                self.chat_history.append(f"🤖 Assistant: Error - {str(e)}")
        
        thread = threading.Thread(target=chat_worker)
        thread.daemon = True
        thread.start()
    
    def _sync_chat(self, message: str, context_frames: List[CANFrame] = None) -> str:
        """Synchronous version of GPT chat."""
        if not self.gpt_agent.client:
            return "GPT agent not configured. Please set your OpenAI API key."
        
        # Build context from recent frames
        context = ""
        if context_frames:
            context = "Recent CAN frames:\n"
            for frame in context_frames[-10:]:  # Last 10 frames
                context += f"ID: 0x{frame.can_id:X}, Data: {frame.data.hex().upper()}\n"
            context += "\n"
        
        # System prompt for CAN bus reverse engineering
        system_prompt = """You are a specialized CAN bus reverse engineering expert with deep knowledge of:
        - OBDII protocols and PIDs
        - J1939 protocols and PGNs  
        - Automotive ECU communication patterns
        - CAN frame analysis and interpretation
        - Common automotive data formats and scaling
        
        Help users analyze CAN bus data, identify protocols, decode messages, and understand ECU behavior.
        Be concise but thorough in your explanations."""
        
        try:
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history (last 10 exchanges)
            messages.extend(self.gpt_agent.conversation_history[-10:])
            
            # Add current context and message
            user_message = context + message
            messages.append({"role": "user", "content": user_message})
            
            response = self.gpt_agent.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=500,
                temperature=0.7
            )
            
            assistant_response = response.choices[0].message.content
            
            # Update conversation history
            self.gpt_agent.conversation_history.append({"role": "user", "content": message})
            self.gpt_agent.conversation_history.append({"role": "assistant", "content": assistant_response})
            
            # Keep only last 20 messages to manage context length
            if len(self.gpt_agent.conversation_history) > 20:
                self.gpt_agent.conversation_history = self.gpt_agent.conversation_history[-20:]
            
            return assistant_response
            
        except Exception as e:
            error_msg = str(e)
            if "insufficient_quota" in error_msg or "429" in error_msg:
                return ("⚠️ OpenAI quota exceeded. Please check your billing at https://platform.openai.com/usage\n\n"
                       "💡 Meanwhile, I can still provide local CAN analysis without GPT. "
                       "Try the 'Analyze Selected Frame' button for basic protocol detection.")
            elif "invalid_api_key" in error_msg:
                return "❌ Invalid API key. Please reconfigure with a valid OpenAI API key."
            else:
                return f"Error communicating with GPT: {error_msg}"
    
    def analyze_selected_frame(self):
        """Analyze the currently selected frame with GPT."""
        row = self.table.currentRow()
        if row < 0:
            self.chat_history.append("\n⚠️ No frame selected. Please select a row in the table.")
            return
        
        # Get frame data from the selected row
        id_item = self.table.item(row, self.COL_ID)
        if not id_item:
            return
        
        try:
            can_id = int(id_item.text().replace("0x", ""), 16)
            data = []
            for i in range(8):
                item = self.table.item(row, self.COL_DATA_START + i)
                val = int(item.text(), 16) if item and item.text() else 0
                data.append(val)
            
            frame = CANFrame(ts=0, can_id=can_id, data=bytes(data))
            
            # Get basic analysis from our local analyzer
            local_analysis = self.gpt_agent.analyze_frame(frame)
            
            self.chat_history.append(f"\n🔍 Analyzing selected frame (ID: 0x{can_id:X})...")
            self.chat_history.append(f"📊 Local Analysis:\n{local_analysis}")
            
            # Send to GPT for deeper analysis
            message = f"Please provide detailed analysis of this CAN frame: ID=0x{can_id:X}, Data={frame.data.hex().upper()}"
            self.chat_input.setText(message)
            self.send_chat_message()
            
        except Exception as e:
            self.chat_history.append(f"\n❌ Error analyzing frame: {str(e)}")
    
    def detect_protocols(self):
        """Analyze recent frames to detect protocols."""
        if not self.recent_frames:
            self.chat_history.append("\n⚠️ No recent frames to analyze. Start playback first.")
            return
        
        # Analyze protocols in recent frames
        protocols = {}
        for frame in self.recent_frames:
            protocol = self.gpt_agent._detect_protocol(frame.can_id)
            protocols[protocol] = protocols.get(protocol, 0) + 1
        
        analysis = "🔍 Protocol Detection Results:\n"
        for protocol, count in sorted(protocols.items(), key=lambda x: x[1], reverse=True):
            analysis += f"- {protocol}: {count} frames\n"
        
        self.chat_history.append(f"\n{analysis}")
        
        # Ask GPT for interpretation
        message = f"I detected these protocols in my CAN data: {', '.join(protocols.keys())}. Can you explain what this means and suggest analysis approaches?"
        self.chat_input.setText(message)
        self.send_chat_message()

    def apply_graph_config(self):
        try:
            can_id_text = self.txt_sig_id.text().strip()
            can_id = int(can_id_text, 16) if can_id_text.lower().startswith('0x') else int(can_id_text, 16)
            # Selected start bit is the currently selected cell number
            idx = self.grid_bits.currentItem()
            if not idx:
                return
            r = idx.row()
            c = idx.column()
            start_bit = r * 8 + (7 - c)
            length = int(self.spn_len_bits.value())
            little = self.chk_little.isChecked()
            signed = self.chk_signed.isChecked()
            scale = float(self.txt_scale.text() or '1')
            bias = float(self.txt_bias.text() or '0')
        except Exception:
            return
        self.graph_cfg = GraphConfig(
            name=self.txt_sig_name.text().strip() or 'Signal',
            can_id=can_id,
            start_bit=start_bit,
            length=length,
            little_endian=little,
            signed=signed,
            scale=scale,
            bias=bias,
        )
        self.graph_enabled = True
        # Reset view for new config
        self.graph_x.clear()
        self.graph_y.clear()
        self.plot_curve.setData([], [])
        try:
            self.plot.enableAutoRange('xy', True)
        except Exception:
            pass

    def clear_graph(self):
        self.graph_enabled = False
        self.graph_cfg = None
        self.graph_x.clear()
        self.graph_y.clear()
        self.plot_curve.setData([], [])
        # Reset axes to initial position
        try:
            self.plot.enableAutoRange('xy', True)
            # Provide a small default window at origin so it's visually reset
            self.plot.setXRange(0, 1, padding=0)
            self.plot.setYRange(0, 1, padding=0)
        except Exception:
            pass

    def on_mode_changed(self, text: str):
        self.mode = text
        # Reset latched structures when switching into latched
        if self.mode == "Latched":
            self.id_row.clear()
            self.id_last_data.clear()
            self.clear_table()

    def on_source_changed(self, text: str):
        self.source = text
        # Reset playback clock
        self.play_start_monotonic = None

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", str(Path.home()), "CSV Files (*.csv)")
        if not path:
            return
        self.csv_player.load(path)
        # Switch source to CSV automatically
        self.cmb_source.setCurrentText("CSV")
        self.play_start_monotonic = None
        # Reset playback index and frequency/history
        self._csv_play_index = 0  # type: ignore[attr-defined]
        self.id_times.clear()
        self.id_row.clear()
        self.id_last_data.clear()
        self.clear_table()
        # Setup seek controls based on CSV duration
        try:
            self.spn_seek.setEnabled(True)
            self.btn_seek.setEnabled(True)
            self.spn_seek.setRange(0.0, max(0.0, float(self.csv_player.duration)))
            self.spn_seek.setValue(0.0)
            # Timeline slider uses milliseconds
            max_ms = int(max(0.0, float(self.csv_player.duration)) * 1000)
            self.sld_timeline.setEnabled(True)
            self.sld_timeline.setMinimum(0)
            self.sld_timeline.setMaximum(max_ms)
            self.sld_timeline.setValue(0)
            self.lbl_timeline_pos.setText("0.000s")
        except Exception:
            pass
        # If GPT is configured, allow CSV analysis
        try:
            self.btn_analyze_csv.setEnabled(bool(self.gpt_agent.client and self.csv_player.frames))
        except Exception:
            pass

    def _build_csv_summary(self, max_ids: int = 12, sample_count: int = 6) -> str:
        """Create a compact summary of the loaded CSV for GPT context.
        Includes file info, duration, frame counts, top IDs with frequencies, suspected protocols,
        timestamp mode, and a small sample of frames.
        """
        frames = self.csv_player.frames
        if not frames:
            return "No CSV loaded."

        total = len(frames)
        duration = self.csv_player.duration
        # ID counts
        id_counts: Dict[int, int] = defaultdict(int)
        for fr in frames:
            id_counts[fr.can_id] += 1
        # Top IDs by count
        top = sorted(id_counts.items(), key=lambda kv: kv[1], reverse=True)[:max_ids]
        # Protocol guesses
        proto_counts: Dict[str, int] = defaultdict(int)
        for can_id, cnt in top:
            proto = self.gpt_agent._detect_protocol(can_id)
            proto_counts[proto] += cnt

        # Build summary text
        lines = []
        if self.csv_player.file_path:
            lines.append(f"CSV file: {Path(self.csv_player.file_path).name}")
        lines.append(f"Frames: {total}")
        lines.append(f"Duration: {duration:.3f} s")
        if self.csv_player.is_epoch and self.csv_player.base_epoch:
            dt0 = datetime.fromtimestamp(self.csv_player.base_epoch, tz=timezone.utc)
            lines.append(f"Timestamp mode: Absolute epoch; start: {dt0.isoformat()}")
        else:
            lines.append("Timestamp mode: Relative seconds from CSV start_time")

        lines.append("Top IDs by count (ID hex | count | freq Hz | suspected protocol):")
        for can_id, cnt in top:
            freq = (cnt / duration) if duration > 0 else 0.0
            proto = self.gpt_agent._detect_protocol(can_id)
            lines.append(f"- 0x{can_id:X} | {cnt} | {freq:.3f} Hz | {proto}")

        # Sample frames: evenly spaced indices
        lines.append("Sample frames (ID hex, data hex):")
        if total > 0:
            step = max(1, total // max(sample_count, 1))
            idxs = list(range(0, total, step))[:sample_count]
            for i in idxs:
                fr = frames[i]
                lines.append(f"- 0x{fr.can_id:X}, {fr.data.hex().upper()}")

        return "\n".join(lines)

    def analyze_csv(self):
        """Summarize the currently loaded CSV and ask GPT for analysis."""
        if not self.gpt_agent.client:
            self.chat_history.append("\n⚠️ GPT not configured. Configure your API key first.")
            return
        if not self.csv_player.frames:
            self.chat_history.append("\n⚠️ No CSV loaded. Use 'Load CSV…' first.")
            return

        summary = self._build_csv_summary()
        self.chat_history.append("\n📄 CSV Summary prepared. Sending to assistant…")
        self.chat_history.append("🤖 Assistant: Thinking...")

        def worker():
            try:
                prompt = (
                    "Analyze this CAN CSV summary. Identify protocols (OBDII/J1939), key IDs, "
                    "signals to investigate, and recommended reverse-engineering steps.\n\n" + summary
                )
                response = self._sync_chat(prompt, context_frames=list(self.recent_frames))
                # Replace the 'Thinking...' line
                self.chat_history.textCursor().movePosition(self.chat_history.textCursor().End)
                cursor = self.chat_history.textCursor()
                cursor.movePosition(cursor.End)
                cursor.movePosition(cursor.StartOfLine, cursor.KeepAnchor)
                cursor.removeSelectedText()
                self.chat_history.append(f"🤖 Assistant: {response}")
            except Exception as e:
                self.chat_history.append(f"🤖 Assistant: Error - {e}")

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def start(self):
        if not self.running:
            self.running = True
            self.play_start_monotonic = time.perf_counter()
            # Choose display epoch base: CSV absolute base if available and epoch-like; else real-time now
            if self.source == "CSV" and self.csv_player.is_epoch and self.csv_player.base_epoch is not None:
                self.display_epoch_base = self.csv_player.base_epoch
            else:
                self.display_epoch_base = time.time()
            self.timer.start()

    def stop(self):
        if self.running:
            self.running = False
            self.timer.stop()

    def clear_table(self):
        self.table.setRowCount(0)
        # Also clear bit view
        self._clear_bit_view()
        self.id_prev_data.clear()
        self.id_counts.clear()

    def reset_playback(self):
        """Reset to the start of the CSV file (if CSV source), and clear table/state."""
        # Clear table/state first
        self.clear_table()
        self.id_times.clear()
        self.id_row.clear()
        self.id_last_data.clear()
        self.id_prev_data.clear()
        self.id_counts.clear()
        # Reset playback index/clock and display-base
        self._csv_play_index = 0  # type: ignore[attr-defined]
        self.play_start_monotonic = time.perf_counter()
        if self.source == "CSV" and self.csv_player.is_epoch and self.csv_player.base_epoch is not None:
            self.display_epoch_base = self.csv_player.base_epoch
        else:
            self.display_epoch_base = time.time()

    def on_tick(self):
        # Determine source frames for this tick
        if self.source == "Simulation":
            frames = self.sim.tick()
        else:
            frames = self._csv_frames_for_now()
        # Keep timeline slider synced with playback when CSV source
        if self.source == "CSV" and self.play_start_monotonic is not None and self.sld_timeline.isEnabled():
            try:
                elapsed = time.perf_counter() - self.play_start_monotonic
                ms = int(max(0.0, min(elapsed, float(self.csv_player.duration))) * 1000)
                # Avoid feedback: do not trigger seek on programmatic update
                self.sld_timeline.blockSignals(True)
                self.sld_timeline.setValue(ms)
                self.sld_timeline.blockSignals(False)
                self.lbl_timeline_pos.setText(f"{ms/1000.0:.3f}s")
            except Exception:
                pass
        for fr in frames:
            # Preserve previous data for bit view
            prev_for_id = self.id_last_data.get(fr.can_id)
            if prev_for_id is not None:
                self.id_prev_data[fr.can_id] = prev_for_id
            self._register_time(fr.can_id, fr.ts)
            if self._should_filter(fr.can_id) or not self._passes_id_filter(fr.can_id):
                continue
            # increment count per ID
            self.id_counts[fr.can_id] = self.id_counts.get(fr.can_id, 0) + 1
            if self.mode == "Continuous":
                self._append_row(fr)
            else:
                self._latched_update(fr)
            # Track last data for bit comparison regardless of mode
            self.id_last_data[fr.can_id] = fr.data
            
            # Store recent frames for GPT context
            self.recent_frames.append(fr)

            # Graphing: if enabled and ID matches, extract value and plot
            if self.graph_enabled and self.graph_cfg and fr.can_id == self.graph_cfg.can_id:
                val = self._extract_signal_value(fr.data, self.graph_cfg)
                if val is not None:
                    self.graph_x.append(fr.ts)
                    self.graph_y.append(val)

        # Update running clock: for CSV, show absolute timestamp of latest displayed frame
        try:
            if self.source == "CSV" and frames:
                last = frames[-1]
                if last.orig_abs_str:
                    self.lbl_clock.setText(last.orig_abs_str)
                else:
                    # Fallback to base epoch + relative seconds
                    epoch = (self.csv_player.base_epoch or self.display_epoch_base) + last.ts
                    dt = datetime.fromtimestamp(epoch)
                    self.lbl_clock.setText(dt.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                # Simulation or no new CSV frame: show elapsed-based clock
                if self.play_start_monotonic is None:
                    elapsed = 0.0
                else:
                    elapsed = time.perf_counter() - self.play_start_monotonic
                self.lbl_clock.setText(self._format_wall_time(elapsed))
        except Exception:
            pass

        # Runtime verification logging: first 3 CSV frames
        try:
            if self.source == "CSV" and frames and self._csv_verify_count < 3:
                last = frames[-1]
                table_ts = self._format_table_time(last)
                clock_ts = self.lbl_clock.text()
                print(f"[VERIFY] CSV9='{last.orig_abs_str}' | table='{table_ts}' | clock='{clock_ts}'")
                self._csv_verify_count += 1
        except Exception:
            pass

        # After processing this batch, update plot
        if self.graph_enabled and self.graph_cfg:
            self.plot_curve.setData(list(self.graph_x), list(self.graph_y))

        # (Sorting of latched table disabled per user request)

        # Optional: cap rows to keep UI responsive
        max_rows = 5000 if self.mode == "Continuous" else 2000
        if self.table.rowCount() > max_rows:
            self.table.removeRow(0)

    # --- Frequency tracking and filtering ---
    def _register_time(self, can_id: int, ts: float):
        dq = self.id_times[can_id]
        dq.append(ts)
        cutoff = ts - self.window_sec
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _freq(self, can_id: int) -> float:
        dq = self.id_times.get(can_id)
        if not dq:
            return 0.0
        return len(dq) / self.window_sec

    def _should_filter(self, can_id: int) -> bool:
        if not self.chk_filter.isChecked():
            return False
        thr = float(self.spn_freq.value())
        return self._freq(can_id) > thr

    # --- Table helpers ---
    def _append_row(self, fr: CANFrame):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._populate_row(row, fr, prev_data=None)

    def _latched_update(self, fr: CANFrame):
        # if ID not in table, add
        row = self.id_row.get(fr.can_id)
        prev = self.id_last_data.get(fr.can_id)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.id_row[fr.can_id] = row
            self._populate_row(row, fr, prev_data=None)
            self.id_last_data[fr.can_id] = fr.data
            return

        # If data identical, only refresh timestamp and frequency
        if prev is not None and prev == fr.data:
            self._update_timestamp_and_freq(row, fr)
            return

        # Overwrite row and color cells based on increase/decrease
        # Save previous for bit-view
        if prev is not None:
            self.id_prev_data[fr.can_id] = prev
        self._populate_row(row, fr, prev_data=prev)
        self.id_last_data[fr.can_id] = fr.data

    def _update_timestamp_and_freq(self, row: int, fr: CANFrame):
        ts_item = QTableWidgetItem(self._format_table_time(fr))
        ts_item.setData(Qt.UserRole, fr.ts)
        try:
            ts_item.setData(Qt.UserRole + 1, fr.orig_abs_str)
        except Exception:
            pass
        ts_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_TIMESTAMP, ts_item)

        freq_item = QTableWidgetItem(f"{self._freq(fr.can_id):0.1f}")
        freq_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_FREQ, freq_item)

        count_val = self.id_counts.get(fr.can_id, 0)
        count_item = QTableWidgetItem(str(count_val))
        count_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_COUNT, count_item)

    def _populate_row(self, row: int, fr: CANFrame, prev_data: bytes | None):
        # timestamp (full datetime)
        ts_item = QTableWidgetItem(self._format_table_time(fr))
        ts_item.setData(Qt.UserRole, fr.ts)
        try:
            ts_item.setData(Qt.UserRole + 1, fr.orig_abs_str)
        except Exception:
            pass
        ts_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_TIMESTAMP, ts_item)

        # frequency
        freq_item = QTableWidgetItem(f"{self._freq(fr.can_id):0.1f}")
        freq_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_FREQ, freq_item)

        # count
        count_val = self.id_counts.get(fr.can_id, 0)
        count_item = QTableWidgetItem(str(count_val))
        count_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_COUNT, count_item)

        # id (show full hex without leading zero padding)
        id_item = QTableWidgetItem(f"0x{fr.can_id:X}")
        id_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, self.COL_ID, id_item)

        # data bytes
        for i in range(8):
            b = fr.data[i] if i < len(fr.data) else 0
            item = QTableWidgetItem(f"{b:02X}")
            item.setTextAlignment(Qt.AlignCenter)
            # Color if in latched with prev_data
            if prev_data is not None and i < len(prev_data):
                pb = prev_data[i]
                if b > pb:
                    item.setBackground(QBrush(QColor(0, 200, 0, 80)))  # greenish
                elif b < pb:
                    item.setBackground(QBrush(QColor(220, 0, 0, 80)))  # reddish
            self.table.setItem(row, self.COL_DATA_START + i, item)

        # If this row is selected, refresh bit view to reflect latest values
        if self.table.currentRow() == row:
            self._refresh_bit_view_from_row(row)

    def on_ts_format_changed(self, _: str):
        # Refresh timestamp column for all rows and update clock immediately
        self._refresh_timestamp_cells()
        try:
            if self.play_start_monotonic is None:
                elapsed = 0.0
            else:
                elapsed = time.perf_counter() - self.play_start_monotonic
            self.lbl_clock.setText(self._format_wall_time(elapsed))
        except Exception:
            pass

    def _refresh_timestamp_cells(self):
        rows = self.table.rowCount()
        for r in range(rows):
            it = self.table.item(r, self.COL_TIMESTAMP)
            if not it:
                continue
            rel_ts = it.data(Qt.UserRole)
            if rel_ts is None:
                continue
            try:
                # Try to use stored original CSV text when applicable
                orig_abs = it.data(Qt.UserRole + 1)
                # Build a temporary CANFrame shim to reuse _format_table_time logic
                fr = CANFrame(ts=float(rel_ts), can_id=0, data=b"\x00"*8, orig_abs_str=orig_abs)
                txt = self._format_table_time(fr)
                it.setText(txt)
            except Exception:
                continue

    def _resort_latched_by_freq(self):
        # Sort by frequency column descending
        self.table.sortItems(self.COL_FREQ, Qt.DescendingOrder)
        # Rebuild id_row mapping according to new row order
        self.id_row.clear()
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, self.COL_ID)
            if not id_item:
                continue
            try:
                can_id = int(id_item.text().replace("0x", ""), 16)
            except Exception:
                continue
            self.id_row[can_id] = r

    # --- Bit view helpers ---
    def _clear_bit_view(self):
        for r in range(8):
            for c in range(8):
                self.bit_table.setItem(r, c, QTableWidgetItem(""))

    def on_selection_changed(self):
        row = self.table.currentRow()
        if row < 0:
            self._clear_bit_view()
            return
        self._refresh_bit_view_from_row(row)

    def _refresh_bit_view_from_row(self, row: int):
        # Extract ID and data bytes from the row
        id_item = self.table.item(row, self.COL_ID)
        if not id_item:
            self._clear_bit_view()
            return
        try:
            can_id = int(id_item.text().replace("0x", ""), 16)
        except Exception:
            self._clear_bit_view()
            return
        data = []
        for i in range(8):
            it = self.table.item(row, self.COL_DATA_START + i)
            val = int(it.text(), 16) if it and it.text() else 0
            data.append(val)
        cur = bytes(data)
        prev = self.id_prev_data.get(can_id)
        self._update_bit_table(cur, prev)

    def _update_bit_table(self, cur: bytes, prev: bytes | None):
        for r in range(8):
            cb = cur[r] if r < len(cur) else 0
            pb = prev[r] if (prev is not None and r < len(prev)) else None
            for c in range(8):
                # b7 at column 0
                bit = (cb >> (7 - c)) & 1
                item = QTableWidgetItem(str(bit))
                item.setTextAlignment(Qt.AlignCenter)
                if pb is not None:
                    pbit = (pb >> (7 - c)) & 1
                    if bit > pbit:
                        item.setBackground(QBrush(QColor(0, 200, 0, 80)))
                    elif bit < pbit:
                        item.setBackground(QBrush(QColor(220, 0, 0, 80)))
                self.bit_table.setItem(r, c, item)

    # --- CSV playback helpers ---
    def _csv_frames_for_now(self) -> List[CANFrame]:
        if not self.csv_player.frames:
            return []
        if self.play_start_monotonic is None:
            self.play_start_monotonic = time.perf_counter()
            self._csv_play_index = 0  # type: ignore[attr-defined]

        elapsed = time.perf_counter() - self.play_start_monotonic
        out: List[CANFrame] = []
        # Emit frames whose ts <= elapsed
        i = getattr(self, "_csv_play_index", 0)
        frs = self.csv_player.frames
        n = len(frs)
        while i < n and frs[i].ts <= elapsed:
            out.append(frs[i])
            i += 1
        self._csv_play_index = i

        # If finished
        if i >= n and self.chk_loop.isChecked():
            # Loop: reset clock and index
            self.play_start_monotonic = time.perf_counter()
            self._csv_play_index = 0
        return out

    # --- Timestamp formatting ---
    def _format_wall_time(self, rel_ts: float) -> str:
        # Determine desired formatting mode
        mode = self.cmb_ts_format.currentText() if hasattr(self, 'cmb_ts_format') else 'Auto'
        csv_is_epoch = getattr(self.csv_player, 'is_epoch', False)
        base_rel = getattr(self.csv_player, 'base_rel_start', None) or 0.0

        def fmt_hms(sec: float) -> str:
            s = max(0.0, sec)
            hours = int(s // 3600)
            s -= hours * 3600
            minutes = int(s // 60)
            s -= minutes * 60
            return f"{hours:02d}:{minutes:02d}:{s:06.3f}"

        if mode == 'Rel seconds':
            return f"{base_rel + rel_ts:.3f}s"
        if mode == 'Rel HH:MM:SS.mmm':
            return fmt_hms(base_rel + rel_ts)
        if mode == 'Absolute':
            # If CSV has absolute timestamps, use its original base epoch
            if self.source == 'CSV' and csv_is_epoch and getattr(self.csv_player, 'base_epoch', None):
                epoch = self.csv_player.base_epoch + rel_ts
                dt = datetime.fromtimestamp(epoch)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                epoch = self.display_epoch_base + rel_ts
                dt = datetime.fromtimestamp(epoch)
                return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _format_table_time(self, fr: CANFrame) -> str:
        """Formatting for the table timestamp column: relative seconds from start_time."""
        return f"{fr.ts:.6f}s"

    # --- ID Filter ---
    def _parse_id_filter_text(self, text: str):
        s: set[int] = set()
        patterns: list[str] = []
        for part in text.split(','):
            p = part.strip()
            if not p:
                continue
            # Normalize remove 0x and upper-case for pattern matching
            p_norm = p.upper()
            if p_norm.startswith('0X'):
                p_norm = p_norm[2:]
            # Wildcard pattern (supports * and ?)
            if '*' in p_norm or '?' in p_norm:
                patterns.append(p_norm)
                continue
            # Try hex integer parse
            try:
                v = int(p, 16)
                s.add(v)
                # If user provided a short hex (prefix), also treat as prefix pattern
                hex_text = p_norm
                if hex_text.startswith('0X'):
                    hex_text = hex_text[2:]
                if 1 <= len(hex_text) < 8:
                    patterns.append(hex_text + '*')
            except Exception:
                # If not valid hex, skip
                continue
        self._id_filter_set = s
        self._id_filter_patterns = patterns

    def _passes_id_filter(self, can_id: int) -> bool:
        if not self.chk_id_filter.isChecked():
            return True
        # If no explicit filters or patterns, pass everything
        if not self._id_filter_set and not self._id_filter_patterns:
            return True
        # Exact numeric match
        if can_id in self._id_filter_set:
            return True
        # Pattern match on uppercase hex without 0x
        hex_id = f"{can_id:X}"
        for pat in self._id_filter_patterns:
            if fnmatch.fnmatchcase(hex_id, pat):
                return True
        return False

    # --- Signal extraction ---
    def _extract_signal_value(self, data: bytes, cfg: 'GraphConfig') -> float | None:
        if not data or cfg.length <= 0 or cfg.length > 64:
            return None
        if cfg.start_bit < 0 or cfg.start_bit > 63:
            return None
        # Make sure extraction does not overflow available 64-bit window
        end_bit = cfg.start_bit + cfg.length - 1
        if end_bit > 63:
            return None
        if cfg.little_endian:
            raw = _bits_to_value_le(data, cfg.start_bit, cfg.length)
        else:
            raw = _bits_to_value_be(data, cfg.start_bit, cfg.length)
        if cfg.signed:
            raw = _apply_signed(raw, cfg.length)
        val = float(raw) * cfg.scale + cfg.bias
        return val

# --- Graphing structures and helpers ---

@dataclass
class GraphConfig:
    name: str
    can_id: int  # 11-bit
    start_bit: int  # 0..63 (0 = LSB of byte0 when little-endian)
    length: int  # number of bits
    little_endian: bool
    signed: bool
    scale: float
    bias: float


def _bits_to_value_le(data: bytes, start_bit: int, length: int) -> int:
    # Little-endian bit numbering: bit 0 = LSB of byte0
    v = int.from_bytes(data.ljust(8, b"\x00"), byteorder="little", signed=False)
    mask = (1 << length) - 1
    return (v >> start_bit) & mask


def _bits_to_value_be(data: bytes, start_bit: int, length: int) -> int:
    # Big-endian bit numbering over 64-bit value: bit 63 = MSB of byte0
    v = int.from_bytes(data.ljust(8, b"\x00"), byteorder="big", signed=False)
    # Convert start_bit in 0..63 to offset from MSB
    bit_from_msb = 63 - start_bit - (length - 1)
    if bit_from_msb < 0:
        bit_from_msb = 0
    mask = (1 << length) - 1
    return (v >> bit_from_msb) & mask


def _apply_signed(val: int, length: int) -> int:
    sign_bit = 1 << (length - 1)
    if val & sign_bit:
        val = val - (1 << length)
    return val



def main():
    app = QApplication(sys.argv)
    w = SnifferWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
