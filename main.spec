# -*- mode: python ; coding: utf-8 -*-

import kivy_deps.sdl2
import kivy_deps.glew
import kivy_deps.angle


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Add these lines:
        ('assets', 'assets'),
        ('data', 'data'),
        # If you have .kv files outside your Python packages:
        ('fd_terminal', 'fd_terminal'),  # if your .kv files are in fd_terminal/
        # Kivy dependencies
        *[(f, ".") for f in kivy_deps.sdl2.dep_bins],
        *[(f, ".") for f in kivy_deps.glew.dep_bins],
        *[(f, ".") for f in kivy_deps.angle.dep_bins],
    ],
    hiddenimports=[
        'kivy', 'kivy.core.window.window_sdl2', 'kivy.core.text', 'kivy.core.image', 'kivy.uix.widget',
        # Add any other Kivy modules you use
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Die-namic Engine Presents',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/images/icon.ico',
)
