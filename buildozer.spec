[app]
title = DieNamic Engine: FDT
package.name = dep_fdt
package.domain = org.dienamicengine
source.dir = .
source.main = main.py
source.include_exts = py,png,jpg,kv,atlas,json,wav,mp3,ogg,ttf,otf
source.include_dirs = assets, data, fd_terminal
source.include_patterns = assets/*,data/*
version = 0.3.6.1
requirements = python3,kivy==2.3.0,cython==0.29.36,plyer,ffpyplayer,android,libffi,openssl
icon.filename = %(source.dir)s/assets/icon.png
android.presplash = %(source.dir)s/assets/logo.png
android.presplash_color = #000000
presplash.filename = %(source.dir)s/assets/logo.png
android.presplash_delay = 2
orientation = portrait
osx.python_version = 3
osx.kivy_version = 1.9.1
fullscreen = 0
android.permissions = READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE
android.api = 35
android.ndk = 25b
android.debug_artifact = apk
android.enable_androidx = True
android.release_artifact = aab
android.release_keystore = de_yfd-release.keystore
android.release_keystore_passwd = 
android.release_keyalias = de_yfd
android.release_keyalias_passwd = 
android.private_storage = True
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
