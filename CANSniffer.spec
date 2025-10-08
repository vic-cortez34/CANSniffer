# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE
from PyInstaller.building.datastruct import Tree

# Hidden imports PyInstaller may miss automatically
hidden = [
    'serial.tools.list_ports',
    'PyQt5.sip',
]
hidden += collect_submodules('serial')  # be safe with pyserial

block_cipher = None

# Include the entire Logger_analyzer/ directory (DBC, CSV, etc.)
logger_tree = Tree('Logger_analyzer', prefix='Logger_analyzer')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Add the Logger_analyzer tree after Analysis so it gets merged into a.datas
a.datas += logger_tree

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='CANSniffer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # set True if you want a console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # set to a .ico path if you have one
)
