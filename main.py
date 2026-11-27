import math
import os
import socket
import struct

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import platform

# ==============================================================================
# [추후 확장 계획 - Expansion Roadmap]
# 1. 실내/실외 2D 맵 및 히트맵 시각화 (AP 좌표·신호 등고선·보행 궤적 표시)
# 2. 걸음(PDR) + 지자기/자이로 센서 융합 (걸음 수·동서남북 방향·거리 정밀화)
# 3. 802.11ax/be (Wi-Fi 6/6E/7) 채널·링크 속도 정밀 파싱 및 로그 저장
# ==============================================================================


def register_korean_font():
    candidates = [
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/NanumGothic.ttf",
        "/system/fonts/DroidSansFallback.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                LabelBase.register(name="AppFont", fn_regular=path)
                return "AppFont"
        except Exception:
            pass
    return "Roboto"


FONT_NAME = register_korean_font()


def make_label(text="", **kwargs):
    kwargs.setdefault("font_name", FONT_NAME)
    return Label(text=text, **kwargs)


def make_button(text="", **kwargs):
    kwargs.setdefault("font_name", FONT_NAME)
    return Button(text=text, **kwargs)


def bind_auto_height(label, min_height=24, pad_y=8, pad_x=8):
    def _update(instance, *args):
        w = max(40, instance.width - pad_x)
        instance.text_size = (w, None)
        th = instance.texture_size[1]
        if th > 0:
            instance.height = max(min_height, th + pad_y)

    label.bind(width=_update, texture_size=_update)
    Clock.schedule_once(lambda dt: _update(label), 0)


def freq_to_band_and_width(freq_mhz, channel_width_enum=None):
    if not freq_mhz or freq_mhz <= 0:
        return "측정 불가", "측정 불가"
    if 2400 <= freq_mhz <= 2500:
        band = "2.4 GHz"
    elif 4900 <= freq_mhz <= 5950:
        band = "5 GHz"
    elif 5925 <= freq_mhz <= 7125:
        band = "6 GHz"
    else:
        band = f"{freq_mhz} MHz"

    width_map = {
        0: "20 MHz",
        1: "40 MHz",
        2: "80 MHz",
        3: "160 MHz",
        4: "80+80 MHz",
        5: "320 MHz",
    }
    if channel_width_enum in width_map:
        width_str = width_map[channel_width_enum]
    else:
        width_str = "20 MHz" if band == "2.4 GHz" else "측정 불가"
    return band, width_str


def estimate_approx_distance_meters(rssi_dbm, freq_mhz):
    if rssi_dbm is None or rssi_dbm >= 0 or not freq_mhz or freq_mhz <= 0:
        return "측정 불가"
    try:
        fspl_1m = 20 * math.log10(freq_mhz) + 20 * math.log10(1.0) - 27.55
        exp = (abs(fspl_1m) - abs(rssi_dbm)) / (10.0 * 2.7)
        dist = max(0.5, min(150.0, math.pow(10.0, exp)))
        return f"약 {dist:.1f}m"
    except Exception:
        return "측정 불가"


def azimuth_to_cardinal(deg):
    if deg is None:
        return "측정 불가"
    dirs = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
    idx = int(((deg % 360) + 22.5) // 45) % 8
    return dirs[idx]


def rssi_to_signal_bars(rssi):
    if rssi is None or rssi <= -100:
        return "측정 불가", 0, (0.45, 0.50, 0.58, 1)
    if rssi >= -52:
        return "강도 4", 4, (0.0, 1.0, 0.62, 1)
    if rssi >= -65:
        return "강도 3", 3, (0.0, 0.88, 1.0, 1)
    if rssi >= -78:
        return "강도 2", 2, (1.0, 0.75, 0.15, 1)
    return "강도 1", 1, (1.0, 0.35, 0.45, 1)


def int_ip_to_str(ip_int):
    if not ip_int or ip_int == 0:
        return "측정 불가"
    try:
        return socket.inet_ntoa(struct.pack("<I", ip_int & 0xFFFFFFFF))
    except Exception:
        return "측정 불가"


class CyberApRadarWidget(Widget):
    """1~4번 노드 일대일 매핑 및 겹침 방지 레이아웃이 적용된 사이버틱 AP 맵"""

    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=256, **kwargs)
        self._ap_name = "측정 불가"
        self._connected = False
        self._nodes = []
        self._labels = []
        for _ in range(13):
            lbl = make_label(
                text="",
                font_size="9.5sp",
                color=(0.88, 0.96, 1.0, 1),
                halign="center",
                valign="middle",
            )
            self._labels.append(lbl)
            self.add_widget(lbl)
        self.bind(pos=self._redraw, size=self._redraw)

    def update_radar(self, connected, connected_ssid, top_aps, link_speed_str):
        self._connected = bool(connected)
        self._ap_name = connected_ssid if (connected and connected_ssid) else "측정 불가"
        self._nodes = []
        for i in range(4):
            node_num = i + 1
            if i < len(top_aps):
                ap = top_aps[i]
                rssi_val = ap.get("rssi")
                bars_txt, bars_cnt, sig_color = rssi_to_signal_bars(rssi_val)
                speed_txt = ap.get("link_speed_str") or link_speed_str or "측정 불가"
                self._nodes.append(
                    {
                        "idx": node_num,
                        "ssid": ap.get("ssid", "측정 불가"),
                        "rssi_str": ap.get("rssi_str", "측정 불가"),
                        "strength_txt": bars_txt,
                        "bars": bars_cnt,
                        "color": sig_color,
                        "speed": speed_txt,
                    }
                )
            else:
                self._nodes.append(
                    {
                        "idx": node_num,
                        "ssid": "측정 불가",
                        "rssi_str": "측정 불가",
                        "strength_txt": "측정 불가",
                        "bars": 0,
                        "color": (0.45, 0.50, 0.58, 1),
                        "speed": "측정 불가",
                    }
                )
        self._redraw()

    def _place_label(self, idx, x, y, w, h, text, halign="center", color=(0.88, 0.96, 1.0, 1)):
        if idx >= len(self._labels):
            return
        lbl = self._labels[idx]
        lbl.pos = (x, y)
        lbl.size = (w, h)
        lbl.text_size = (max(24, w - 4), max(12, h))
        lbl.halign = halign
        lbl.valign = "middle"
        lbl.color = color
        lbl.text = text

    def _draw_router_icon(self, cx, cy, active=False):
        base_color = (0.0, 0.90, 1.0, 1) if active else (0.35, 0.45, 0.58, 0.9)
        body_w, body_h = 56, 18
        bx, by = cx - body_w * 0.5, cy - body_h * 0.5

        Color(0.06, 0.11, 0.19, 1)
        Rectangle(pos=(bx, by), size=(body_w, body_h))
        Color(*base_color)
        Line(rectangle=(bx, by, body_w, body_h), width=1.4)

        for side in (-1, 1):
            ax = cx + side * 18
            Line(points=[ax, by + body_h, ax, by + body_h + 12], width=1.5)
            Ellipse(pos=(ax - 2.5, by + body_h + 11), size=(5, 5))

        for k in range(3):
            lx = bx + 9 + k * 14
            if active:
                Color(0.0, 1.0, 0.62, 1)
            else:
                Color(0.35, 0.42, 0.50, 0.8)
            Ellipse(pos=(lx, by + 6), size=(5, 5))

    def _draw_signal_waves(self, cx, cy, bars, color, outward_angle_deg):
        if bars <= 0:
            return
        Color(color[0], color[1], color[2], 0.85)
        span = 34
        for b in range(bars):
            r = 14 + b * 6
            Line(circle=(cx, cy, r, outward_angle_deg - span, outward_angle_deg + span), width=1.4)

    def _draw_signal_bar_meter(self, x, y, bars, color):
        for b in range(4):
            bh = 5 + b * 3
            bx = x + b * 6
            if b < bars:
                Color(*color)
            else:
                Color(0.20, 0.26, 0.35, 0.7)
            Rectangle(pos=(bx, y), size=(4, bh))

    def _redraw(self, *args):
        self.canvas.before.clear()
        w, h = self.width, self.height
        x0, y0 = self.x, self.y
        if w <= 60 or h <= 60:
            return

        with self.canvas.before:
            Color(0.04, 0.06, 0.11, 1)
            Rectangle(pos=(x0, y0), size=(w, h))

            Color(0.09, 0.16, 0.26, 0.55)
            step_x = max(28, w / 10.0)
            step_y = max(24, h / 8.0)
            gx = x0 + step_x
            while gx < x0 + w:
                Line(points=[gx, y0, gx, y0 + h], width=0.8)
                gx += step_x
            gy = y0 + step_y
            while gy < y0 + h:
                Line(points=[x0, gy, x0 + w, gy], width=0.8)
                gy += step_y

            Color(0.0, 0.82, 0.98, 0.75)
            Line(rectangle=(x0 + 3, y0 + 3, w - 6, h - 6), width=1.2)

            ap_cx = x0 + w * 0.5
            ap_cy = y0 + h - 38
            self._draw_router_icon(ap_cx, ap_cy, active=self._connected)
            self._place_label(
                0,
                ap_cx - 76,
                ap_cy - 24,
                152,
                16,
                f"AP: {self._ap_name[:14]}",
                color=(0.35, 0.95, 1.0, 1),
            )

            ph_w = min(144, max(104, w * 0.35))
            ph_h = max(78, h * 0.34)
            ph_x = x0 + (w - ph_w) * 0.5
            ph_y = y0 + (h - ph_h) * 0.40
            Color(0
