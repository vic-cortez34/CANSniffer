#!/usr/bin/env python3
"""
GUI for OBD/UART configuration and protocol detection + CAN monitor (ATMA).

- Select COM port and baud.
- Run detection sequence: ATZ, ATWS, ATE0, ATS0, ATSP0, STPRS, 0100.
- If OBD found, run STPRS again, map to ATSPx, set it, then start ATMA.
- If J1939 suspected, run ATH1, STP 42, 00FECA and report.
- Stream ATMA messages to the log view. Stop monitoring via button.

Requires: pyserial
Tkinter is included with standard Python on Windows.
"""

import threading
import time
import queue
import sys
from typing import Optional, Sequence

import serial
from serial.tools import list_ports

import tkinter as tk
from tkinter import ttk, messagebox


# ------------------- Serial helpers -------------------

def pick_ports() -> list[str]:
    return [p.device for p in list_ports.comports()]


def write_cmd(ser: serial.Serial, cmd: str) -> None:
    if not cmd.endswith("\r\n"):
        to_send = (cmd + "\r\n").encode("ascii")
    else:
        to_send = cmd.encode("ascii")
    ser.reset_input_buffer()
    ser.write(to_send)
    ser.flush()


def _read_until_patterns(
    ser: serial.Serial,
    patterns: Sequence[bytes],
    timeout: float,
    interchar_timeout: float = 0.2,
) -> bytes:
    deadline = time.time() + timeout
    buf = bytearray()
    ser.timeout = interchar_timeout
    while time.time() < deadline:
        chunk = ser.read(1024)
        if chunk:
            buf.extend(chunk)
            lower = bytes(buf).lower()
            for pat in patterns:
                if pat.lower() in lower:
                    return bytes(buf)
        else:
            pass
    return bytes(buf)


def read_until_prompt(ser: serial.Serial, timeout: float = 5.0) -> str:
    raw = _read_until_patterns(ser, patterns=[b">"], timeout=timeout)
    text = raw.decode("ascii", errors="ignore")
    return text.replace("\r\r", "\r").replace("\r\n", "\n").replace("\r", "\n")


def read_until_crcr(ser: serial.Serial, timeout: float = 5.0) -> str:
    raw = _read_until_patterns(ser, patterns=[b"\r\r"], timeout=timeout)
    text = raw.decode("ascii", errors="ignore")
    return text.replace("\r\r", "\n").replace("\r\n", "\n").replace("\r", "\n")


def contains_token(text: str, token: str) -> bool:
    return token.lower() in text.lower()


def protocol_to_atsp(stprs_text: str) -> Optional[str]:
    mapping = {
        "auto, sae j1850 pwm": "ATSP1",
        "auto, sae j1850 vpw": "ATSP2",
        "auto, iso 9141-2": "ATSP3",
        "auto, iso 14230-4 (kwp fast)": "ATSP5",
        "auto, iso 14230-4 (kwp)": "ATSP4",
        "auto, iso 15765-4 (can 11/500)": "ATSP6",
        "auto, iso 15765-4 (can 29/500)": "ATSP7",
        "auto, iso 15765-4 (can 11/250)": "ATSP8",
        "auto, iso 15765-4 (can 29/250)": "ATSP9",
    }
    low = stprs_text.lower()
    for key, atsp in mapping.items():
        if key in low:
            return atsp
    return None


# ------------------- Worker logic -------------------

class ProtocolWorker(threading.Thread):
    def __init__(self, port: str, baud: int, log_q: queue.Queue[str], status_q: queue.Queue[str]):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.log_q = log_q
        self.status_q = status_q
        self.stop_event = threading.Event()
        self.ser: Optional[serial.Serial] = None

    def log(self, text: str) -> None:
        self.log_q.put(text)

    def set_status(self, text: str) -> None:
        self.status_q.put(text)

    def stop(self):
        self.stop_event.set()
        try:
            if self.ser and self.ser.is_open:
                # Attempt to break out of ATMA by closing port
                self.ser.close()
        except Exception:
            pass

    def run(self):
        try:
            self.set_status(f"Opening {self.port} @ {self.baud} ...")
            self.ser = serial.Serial(self.port, self.baud, timeout=1.0)
            time.sleep(0.2)
            self.set_status("Connected")
            ser = self.ser

            # Init sequence
            for cmd in ["ATZ", "ATWS", "ATE0", "ATS0", "ATSP0"]:
                if self.stop_event.is_set():
                    return
                self.log(f">> {cmd}")
                write_cmd(ser, cmd)
                resp = read_until_prompt(ser, timeout=8.0)
                self.log(resp.strip())

            # STPRS (CRCR)
            self.log(">> STPRS")
            write_cmd(ser, "STPRS")
            stprs1 = read_until_crcr(ser, timeout=8.0)
            self.log(stprs1.strip())

            # 0100
            self.log(">> 0100")
            write_cmd(ser, "0100")
            resp_0100 = read_until_prompt(ser, timeout=12.0)
            self.log(resp_0100.strip())

            normalized = resp_0100.replace(' ', '')
            is_obd = '100' in normalized

            if is_obd:
                self.log("Result: OBD protocol detected (0100 response contains '100').")
                # Ask STPRS again
                self.log(">> STPRS")
                write_cmd(ser, "STPRS")
                stprs2 = read_until_crcr(ser, timeout=8.0)
                self.log(stprs2.strip())

                # Map and set protocol
                atsp = protocol_to_atsp(stprs2)
                if atsp:
                    self.log(f"Configuring protocol explicitly via {atsp} ...")
                    self.log(f">> {atsp}")
                    write_cmd(ser, atsp)
                    resp_atsp = read_until_prompt(ser, timeout=8.0)
                    self.log(resp_atsp.strip())
                    # Notify GUI with detection result
                    self.set_status(f"POPUP: OBD protocol detected. STPRS: {stprs2.strip()} | Set {atsp} successfully.")

                    # Protocol configured successfully - ready for custom commands
                    self.log("Protocol configuration complete. Ready for custom commands.")
                    self.set_status("Ready - Protocol configured")
                else:
                    self.log("STPRS did not match an AUTO protocol requiring ATSP1-9. Skipping explicit set.")
                    self.set_status(f"POPUP: OBD detected, but STPRS did not match a known AUTO protocol.\nSTPRS: {stprs2.strip()}")
                    self.set_status("Ready - Protocol detected")
                
                # Keep connection alive and wait for stop event
                while not self.stop_event.is_set():
                    time.sleep(0.1)
                return

            # Suspected J1939
            self.log("Result: OBD protocol not confirmed by 0100. Suspecting J1939-based protocol...")
            for cmd in ["ATH1", "STP 42"]:
                if self.stop_event.is_set():
                    return
                self.log(f">> {cmd}")
                write_cmd(ser, cmd)
                resp = read_until_prompt(ser, timeout=8.0)
                self.log(resp.strip())

            self.log(">> 00FECA")
            write_cmd(ser, "00FECA")
            resp_feca = read_until_prompt(ser, timeout=8.0)
            self.log(resp_feca.strip())

            if contains_token(resp_feca, "FECA"):
                self.log("Result: J1939-based protocol confirmed (FECA seen in response).")
                self.set_status("POPUP: J1939-based protocol confirmed (FECA seen in response).")
                # Protocol configured successfully - ready for custom commands
                self.log("J1939 protocol configuration complete. Ready for custom commands.")
                self.set_status("Ready - J1939 protocol configured")
            else:
                self.log("Result: J1939-based protocol NOT confirmed (FECA not seen).")
                self.set_status("POPUP: J1939-based protocol NOT confirmed (FECA not seen).")
                self.set_status("Ready - Protocol detection complete")
            
            # Keep connection alive and wait for stop event
            while not self.stop_event.is_set():
                time.sleep(0.1)
        except Exception as e:
            self.set_status("Error")
            self.log(f"Error: {e}")
        finally:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass


# ------------------- GUI -------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OBD Protocol Setup & Monitor")
        self.geometry("900x600")

        self.log_q: queue.Queue[str] = queue.Queue()
        self.status_q: queue.Queue[str] = queue.Queue()
        self.worker: Optional[ProtocolWorker] = None

        self._build_ui()
        self._poll_queues()

    def _build_ui(self):
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Top controls
        top = ttk.Frame(frm)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Port:").pack(side=tk.LEFT)
        self.port_cmb = ttk.Combobox(top, width=15, state="readonly")
        self.port_cmb.pack(side=tk.LEFT, padx=5)
        self._refresh_ports()

        ttk.Button(top, text="Refresh", command=self._refresh_ports).pack(side=tk.LEFT)

        ttk.Label(top, text="Baud:").pack(side=tk.LEFT, padx=(15, 0))
        self.baud_var = tk.StringVar(value="115200")
        ttk.Entry(top, textvariable=self.baud_var, width=10).pack(side=tk.LEFT, padx=5)

        self.run_btn = ttk.Button(top, text="Run Detection", command=self._start_worker)
        self.run_btn.pack(side=tk.LEFT, padx=(15, 5))
        self.stop_btn = ttk.Button(top, text="Stop Monitoring", command=self._stop_worker, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.RIGHT)

        # Log area
        mid = ttk.Frame(frm)
        mid.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.text = tk.Text(mid, wrap=tk.NONE)
        yscroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.text.yview)
        xscroll = ttk.Scrollbar(mid, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        # Bottom controls
        bottom = ttk.Frame(frm)
        bottom.pack(fill=tk.X, pady=10)
        ttk.Button(bottom, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Copy Log", command=self._copy_log).pack(side=tk.LEFT, padx=5)
        
        # Command input section
        cmd_frame = ttk.Frame(bottom)
        cmd_frame.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))
        
        ttk.Label(cmd_frame, text="Custom Command:").pack(side=tk.LEFT)
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(cmd_frame, textvariable=self.cmd_var, width=20)
        self.cmd_entry.pack(side=tk.LEFT, padx=5)
        self.cmd_entry.bind('<Return>', self._send_custom_command)
        
        self.send_btn = ttk.Button(cmd_frame, text="Send", command=self._send_custom_command)
        self.send_btn.pack(side=tk.LEFT)

    def _refresh_ports(self):
        ports = pick_ports()
        self.port_cmb["values"] = ports
        if ports and not self.port_cmb.get():
            self.port_cmb.set(ports[0])

    def _start_worker(self):
        if self.worker is not None:
            messagebox.showinfo("Info", "Worker already running.")
            return
        port = self.port_cmb.get()
        if not port:
            messagebox.showerror("Error", "Select a COM port.")
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid baud rate.")
            return
        self.text.insert(tk.END, f"\n--- Starting on {port} @ {baud} ---\n")
        self.text.see(tk.END)
        self.worker = ProtocolWorker(port, baud, self.log_q, self.status_q)
        self.worker.start()
        self.run_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def _stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.run_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.text.delete("1.0", tk.END)

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", tk.END))
        self.update()  # keep clipboard after window closes
    
    def _send_custom_command(self, event=None):
        """Send a custom command to the serial port if worker is connected."""
        if not self.worker or not self.worker.ser or not self.worker.ser.is_open:
            messagebox.showerror("Error", "No active serial connection. Run detection first.")
            return
            
        cmd = self.cmd_var.get().strip()
        if not cmd:
            return
            
        try:
            # Log the command being sent
            self.text.insert(tk.END, f">> {cmd}\n")
            self.text.see(tk.END)
            
            # Send command through the worker's serial connection
            write_cmd(self.worker.ser, cmd)
            
            # Read response (try both prompt and CRCR endings)
            try:
                if cmd.upper() == "STPRS":
                    response = read_until_crcr(self.worker.ser, timeout=5.0)
                else:
                    response = read_until_prompt(self.worker.ser, timeout=5.0)
                
                # Display response
                if response.strip():
                    self.text.insert(tk.END, f"{response.strip()}\n")
                    self.text.see(tk.END)
                    
            except Exception as e:
                self.text.insert(tk.END, f"Error reading response: {e}\n")
                self.text.see(tk.END)
            
            # Clear the command entry
            self.cmd_var.set("")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send command: {e}")

    def _poll_queues(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.text.insert(tk.END, msg + "\n")
                self.text.see(tk.END)
        except queue.Empty:
            pass
        try:
            while True:
                st = self.status_q.get_nowait()
                if isinstance(st, str) and st.startswith("POPUP:"):
                    # Show detection result popup; keep status label unchanged
                    body = st[len("POPUP:"):].strip()
                    messagebox.showinfo("Detection Result", body)
                else:
                    self.status_var.set(st)
        except queue.Empty:
            pass
        self.after(50, self._poll_queues)


if __name__ == "__main__":
    app = App()
    app.mainloop()
