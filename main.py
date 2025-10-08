#!/usr/bin/env python3
"""
CAN Sniffer & OBD Protocol Setup Tool

Combines OBD protocol detection with CAN bus monitoring and analysis.
"""

import sys
import time
import queue
import threading
from typing import Optional

import serial
from serial.tools import list_ports

# PyQt5 imports
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QStatusBar, QMessageBox,
    QLineEdit
)

# Import CAN analysis components
from Logger_analyzer.can_sniffer_sim import SnifferWindow

# Import protocol detection components
from obd_protocol_gui import write_cmd, read_until_prompt, read_until_crcr, contains_token, protocol_to_atsp

class ProtocolWorker(QObject):
    """Worker thread for OBD protocol detection and configuration."""
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    protocol_detected = pyqtSignal(str, str)  # protocol_name, details
    
    def __init__(self, port: str, baud: int, log_q: queue.Queue, status_q: queue.Queue):
        super().__init__()
        self.port = port
        self.baud = baud
        self.log_q = log_q
        self.status_q = status_q
        self.running = False
        self.ser = None
        self.stop_event = threading.Event()
    
    def log(self, message: str) -> None:
        """Log a message to the queue."""
        self.log_q.put(message)
        self.log_signal.emit(message)
    
    def set_status(self, status: str) -> None:
        """Update the status in the queue."""
        self.status_q.put(status)
        self.status_signal.emit(status)
    
    def stop(self) -> None:
        """Stop the worker thread."""
        self.running = False
        self.stop_event.set()
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception as e:
            self.log(f"Error closing serial port: {e}")
    
    def run(self) -> None:
        """Main worker thread function."""
        try:
            self.running = True
            self.set_status(f"Opening {self.port} @ {self.baud}...")
            self.ser = serial.Serial(self.port, self.baud, timeout=1.0)
            time.sleep(0.2)
            self.set_status("Connected")
            
            # Run protocol detection sequence
            self._run_detection()
            
            # Keep thread alive while running
            while self.running and not self.stop_event.is_set():
                time.sleep(0.1)
                
        except Exception as e:
            self.set_status(f"Error: {str(e)}")
            self.log(f"Error in worker thread: {str(e)}")
        finally:
            self.running = False
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception as e:
                self.log(f"Error closing serial port: {e}")
    
    def _run_detection(self) -> None:
        """Run the protocol detection sequence."""
        if not self.ser or not self.ser.is_open:
            return
            
        try:
            # Init sequence
            for cmd in ["ATZ", "ATWS", "ATE0", "ATS0", "ATSP0"]:
                if self.stop_event.is_set():
                    return
                self.log(f">> {cmd}")
                write_cmd(self.ser, cmd)
                resp = read_until_prompt(self.ser, timeout=8.0)
                self.log(resp.strip())

            # STPRS (CRCR)
            self.log(">> STPRS")
            write_cmd(self.ser, "STPRS")
            stprs1 = read_until_crcr(self.ser, timeout=8.0)
            self.log(stprs1.strip())

            # 0100
            self.log(">> 0100")
            write_cmd(self.ser, "0100")
            resp_0100 = read_until_prompt(self.ser, timeout=12.0)
            self.log(resp_0100.strip())

            normalized = resp_0100.replace(' ', '')
            is_obd = '100' in normalized

            if is_obd:
                self.log("Result: OBD protocol detected (0100 response contains '100').")
                # Ask STPRS again
                self.log(">> STPRS")
                write_cmd(self.ser, "STPRS")
                stprs2 = read_until_crcr(self.ser, timeout=8.0)
                self.log(stprs2.strip())

                # Map and set protocol
                atsp = protocol_to_atsp(stprs2)
                if atsp:
                    self.log(f"Configuring protocol explicitly via {atsp}...")
                    self.log(f">> {atsp}")
                    write_cmd(self.ser, atsp)
                    resp_atsp = read_until_prompt(self.ser, timeout=8.0)
                    self.log(resp_atsp.strip())
                    self.protocol_detected.emit("OBD", f"OBD Protocol: {stprs2.strip()}\nSet {atsp} successfully.")
                else:
                    self.log("STPRS did not match an AUTO protocol requiring ATSP1-9. Skipping explicit set.")
                    self.protocol_detected.emit("OBD", "OBD detected, but STPRS did not match a known AUTO protocol.")
                
                return

            # Suspected J1939
            self.log("Result: OBD protocol not confirmed by 0100. Suspecting J1939-based protocol...")
            
            # Send J1939 initialization commands
            for cmd in ["ATH1", "STP 42"]:
                if self.stop_event.is_set():
                    return
                self.log(f">> {cmd}")
                write_cmd(self.ser, cmd)
                resp = read_until_prompt(self.ser, timeout=8.0)
                self.log(resp.strip())

            # Try STPRS for J1939 protocol detection
            self.log(">> STPRS")
            write_cmd(self.ser, "STPRS")
            stprs_resp = read_until_crcr(self.ser, timeout=8.0).strip()
            self.log(stprs_resp)
            
            # If response is STOPPED, send STPRS again to get actual protocol
            if stprs_resp.upper() == "STOPPED":
                self.log("Received STOPPED, sending STPRS again...")
                write_cmd(self.ser, "STPRS")
                stprs_resp = read_until_crcr(self.ser, timeout=8.0).strip()
                self.log(stprs_resp)
            
            # Check for FECA to confirm J1939
            self.log(">> 00FECA")
            write_cmd(self.ser, "00FECA")
            resp_feca = read_until_prompt(self.ser, timeout=8.0)
            self.log(resp_feca.strip())

            protocol_info = []
            
            if contains_token(resp_feca, "FECA"):
                protocol_info.append("J1939-based protocol confirmed (FECA seen in response).")
                
                # Send STPRS after confirming J1939 and keep retrying if response is STOPPED
                max_retries = 3
                stprs_resp = ""
                for attempt in range(max_retries):
                    self.log(f">> STPRS (Attempt {attempt + 1}/{max_retries})")
                    write_cmd(self.ser, "STPRS")
                    stprs_resp = read_until_crcr(self.ser, timeout=8.0).strip()
                    self.log(stprs_resp)
                    
                    if "STOPPED" not in stprs_resp.upper():
                        break
                        
                    self.log("Received response containing 'STOPPED', retrying STPRS...")
                    time.sleep(0.5)  # Small delay before retry
                
                if "STOPPED" in stprs_resp.upper():
                    self.log("Warning: Reached maximum retries for STPRS")
                
                protocol_info.append(f"STPRS Response: {stprs_resp}")
                
                # Emit protocol detected signal with all info
                protocol_text = "\n\n".join(protocol_info)
                self.protocol_detected.emit("J1939", protocol_text)
            else:
                protocol_info.append("J1939-based protocol NOT confirmed (FECA not seen).")
                protocol_text = "\n\n".join(protocol_info)
                self.protocol_detected.emit("Unknown", protocol_text)
                
        except Exception as e:
            self.log(f"Error in protocol detection: {str(e)}")
            self.set_status(f"Error: {str(e)}")

class MainWindow(QMainWindow):
    """Main application window with tabbed interface."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN Sniffer & OBD Protocol Setup")
        self.resize(1200, 800)
        
        # Serial connection state
        self.serial_worker = None
        self.serial_thread = None
        self.sniffer_window = None
        
        self._init_ui()
    
    def _init_ui(self):
        """Initialize the user interface."""
        # Create tab widget
        self.tabs = QTabWidget()
        
        # Create tabs
        self.protocol_tab = self._create_protocol_tab()
        self.monitor_tab = self._create_monitor_tab()
        
        # Add tabs to the tab widget
        self.tabs.addTab(self.protocol_tab, "OBD Protocol Setup")
        self.tabs.addTab(self.monitor_tab, "CAN Monitor")
        
        # Set the central widget
        self.setCentralWidget(self.tabs)
        
        # Create status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
    
    def _create_protocol_tab(self):
        """Create the OBD Protocol Setup tab."""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Top controls
        top = QHBoxLayout()
        
        # Port selection
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_ports()
        port_layout.addWidget(self.port_combo)
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_ports)
        port_layout.addWidget(refresh_btn)
        
        # Baud rate selection
        port_layout.addWidget(QLabel("Baud:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.baud_combo.setCurrentText("115200")
        port_layout.addWidget(self.baud_combo)
        
        # Connect button
        self.connect_btn = QPushButton("Connect & Detect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        port_layout.addWidget(self.connect_btn)
        
        top.addLayout(port_layout)
        layout.addLayout(top)
        
        # Log area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFontFamily("Courier")
        layout.addWidget(self.log_text)
        
        # Protocol info
        self.protocol_info = QTextEdit()
        self.protocol_info.setReadOnly(True)
        self.protocol_info.setMaximumHeight(100)
        self.protocol_info.setPlaceholderText("Protocol information will appear here...")
        layout.addWidget(self.protocol_info)
        
        # Bottom buttons
        btn_layout = QHBoxLayout()
        
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.clear_log)
        btn_layout.addWidget(clear_btn)
        
        # Command input
        cmd_layout = QHBoxLayout()
        cmd_layout.addWidget(QLabel("Command:"))
        self.cmd_input = QLineEdit()
        self.cmd_input.returnPressed.connect(self.send_command)
        cmd_layout.addWidget(self.cmd_input)
        
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send_command)
        cmd_layout.addWidget(send_btn)
        
        btn_layout.addLayout(cmd_layout)
        layout.addLayout(btn_layout)
        
        tab.setLayout(layout)
        return tab
    
    def on_protocol_detected(self, protocol_type, message):
        """Handle protocol detection results."""
        title = f"{protocol_type} Protocol Detected" if protocol_type != "Unknown" else "Protocol Detection"
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle(title)
        msg.setText("Protocol Detection Results")
        msg.setInformativeText(message)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
    
    def _create_monitor_tab(self):
        """Create the CAN Monitor tab."""
        tab = QWidget()
        layout = QVBoxLayout()
        
        # Add a button to open the CAN monitor
        self.open_monitor_btn = QPushButton("Open CAN Monitor")
        self.open_monitor_btn.clicked.connect(self.open_can_monitor)
        layout.addWidget(self.open_monitor_btn)
        
        # Add a label with instructions
        info_label = QLabel(
            "Click the button above to open the CAN bus monitor. "
            "The monitor allows you to view and analyze CAN traffic."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Add a status label for the monitor
        self.monitor_status = QLabel("Status: Not connected")
        layout.addWidget(self.monitor_status)
        
        # Add stretch to push content to the top
        layout.addStretch()
        
        tab.setLayout(layout)
        return tab
    
    def refresh_ports(self):
        """Refresh the list of available serial ports."""
        current = self.port_combo.currentText()
        self.port_combo.clear()
        ports = [p.device for p in list_ports.comports()]
        if ports:
            self.port_combo.addItems(ports)
            if current in ports:
                self.port_combo.setCurrentText(current)
    
    def toggle_connection(self):
        """Toggle the serial connection."""
        if self.serial_worker and self.serial_worker.running:
            self.disconnect_serial()
        else:
            self.connect_serial()
    
    def connect_serial(self):
        """Connect to the selected serial port and start protocol detection."""
        port = self.port_combo.currentText()
        if not port:
            self.log_message("Error: No port selected")
            return
            
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            self.log_message(f"Error: Invalid baud rate {self.baud_combo.currentText()}")
            return
        
        # Disconnect if already connected
        if self.serial_worker:
            self.disconnect_serial()
        
        # Create and start worker thread
        self.serial_worker = ProtocolWorker(port, baud, queue.Queue(), queue.Queue())
        self.serial_worker.running = True  # Set running state
        self.serial_worker.protocol_detected.connect(self.on_protocol_detected)
        self.serial_thread = threading.Thread(target=self.serial_worker.run, daemon=True)
        
        # Start the worker
        self.serial_thread.start()
        
        # Start a timer to check for updates
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_worker_updates)
        self.timer.start(100)  # Check every 100ms
        
        # Update UI
        self.connect_btn.setText("Disconnect")
        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)
        
        self.log_message(f"Connecting to {port} @ {baud}...")
    
    def check_worker_updates(self):
        """Check for updates from the worker thread."""
        if not self.serial_worker:
            return
            
        # Check for log messages
        try:
            while True:
                msg = self.serial_worker.log_q.get_nowait()
                self.log_message(msg)
        except queue.Empty:
            pass
            
        # Check for status updates
        try:
            while True:
                status = self.serial_worker.status_q.get_nowait()
                self.update_status(status)
        except queue.Empty:
            pass
    
    def disconnect_serial(self):
        """Disconnect from the serial port and stop the worker thread."""
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
            
        if self.serial_worker:
            self.serial_worker.stop()
            self.serial_worker = None
        
        if self.serial_thread and self.serial_thread.is_alive():
            self.serial_thread.join(timeout=1.0)
        
        # Update UI
        self.connect_btn.setText("Connect & Detect")
        self.port_combo.setEnabled(True)
        self.baud_combo.setEnabled(True)
        self.update_status("Disconnected")
    
    def send_command(self):
        """Send a custom command to the serial port."""
        if not self.serial_worker or not self.serial_worker.running:
            self.log_message("Error: Not connected to serial port")
            return
            
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
            
        try:
            # Log the command
            self.log_message(f">> {cmd}")
            
            # Send the command
            write_cmd(self.serial_worker.ser, cmd)
            
            # Read response based on command type
            if cmd.upper() == "STPRS":
                response = read_until_crcr(self.serial_worker.ser, timeout=5.0)
            else:
                response = read_until_prompt(self.serial_worker.ser, timeout=5.0)
            
            # Display response
            if response.strip():
                self.log_message(response.strip())
            
            # Clear the input
            self.cmd_input.clear()
            
        except Exception as e:
            self.log_message(f"Error sending command: {e}")
    
    def open_can_monitor(self):
        """Open the CAN monitor window."""
        if not self.sniffer_window:
            self.sniffer_window = SnifferWindow()
            self.sniffer_window.show()
        else:
            self.sniffer_window.showNormal()
            self.sniffer_window.activateWindow()
    
    def log_message(self, message: str):
        """Add a message to the log."""
        self.log_text.append(message)
    
    def update_status(self, status: str):
        """Update the status bar and log."""
        self.status_bar.showMessage(status)
        
        # Update monitor tab status if protocol is detected
        if "OBD" in status or "J1939" in status:
            self.monitor_status.setText(f"Status: Ready ({status})")
    
    def clear_log(self):
        """Clear the log window."""
        self.log_text.clear()
    
    def closeEvent(self, event):
        """Handle window close event."""
        # Disconnect serial if connected
        if self.serial_worker and self.serial_worker.running:
            self.disconnect_serial()
        
        # Close CAN monitor window if open
        if self.sniffer_window:
            self.sniffer_window.close()
        
        event.accept()

def main():
    """Main application entry point."""
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle('Fusion')
    
    # Create and show the main window
    window = MainWindow()
    window.show()
    
    # Start the event loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
