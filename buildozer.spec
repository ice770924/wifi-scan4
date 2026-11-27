[app]
title = WiFi Tracker S10
package.name = wifitrackers10
package.domain = org.personal.wifitracker
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.2.0

requirements = python3,kivy==2.3.0,pyjnius,requests,urllib3,certifi,charset-normalizer,idna
p4a.branch = release-2024.01.21

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_STATE,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION
android.api = 31
android.minapi = 31
android.ndk = 25b
android.ndk_api = 31
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
