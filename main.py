import math
import os
import socket
import struct
import traceback

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
            if path and os.path.exists(path):
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
        try:
            w = max(40, float(instance.width) - pad_x)
            instance.text_size = (w, None)
            th = float(instance.texture_size[1])
            if th > 0:
                instance.height = max(min_height, th + pad_y)
        except Exception:
            pass

    label.bind(width=_update, texture_size=_update)
    Clock.schedule_once(lambda dt: _update(label), 0.05)


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
    """확장된 빨간 박스 영역 내 AP·4개 노드·분홍 화살표 방향 신호선·단말 프레임 시각화"""

    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=340, **kwargs)
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
        self.update_radar(False, "측정 불가", [], "측정 불가")
        self.bind(pos=self._redraw, size=self._redraw)

    def update_radar(self, connected, connected_ssid, top_aps, link_speed_str):
        self._connected = bool(connected)
        self._ap_name = connected_ssid if (connected and connected_ssid) else "측정 불가"
        self._nodes = []
        safe_aps = top_aps if isinstance(top_aps, list) else []
        for i in range(4):
            node_num = i + 1
            if i < len(safe_aps):
                ap = safe_aps[i]
                rssi_val = ap.get("rssi")
                bars_txt, bars_cnt, sig_color = rssi_to_signal_bars(rssi_val)
                speed_txt = ap.get("link_speed_str") or link_speed_str or "측정 불가"
                self._nodes.append(
                    {
                        "idx": node_num,
                        "ssid": str(ap.get("ssid", "측정 불가")),
                        "rssi_str": str(ap.get("rssi_str", "측정 불가")),
                        "strength_txt": bars_txt,
                        "bars": bars_cnt,
                        "color": sig_color,
                        "speed": str(speed_txt),
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
        body_w, body_h = 58, 18
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

    def _draw_directed_signal_line(self, sx, sy, tx, ty, bars, color):
        """분홍 화살표 방향을 따라 AP 기준점에서 각 노드로 향하는 신호선 및 화살촉 생성"""
        dx = tx - sx
        dy = ty - sy
        dist = math.hypot(dx, dy)
        if dist < 4:
            return
        ux = dx / dist
        uy = dy / dist
        px = -uy
        py = ux

        # 시작·끝 여백을 두어 노드 박스와 겹치지 않게 선 연결
        start_x = sx + ux * 16
        start_y = sy + uy * 16
        end_x = tx - ux * 22
        end_y = ty - uy * 22

        # 메인 신호 선(분홍 포인트 라인 + 신호 강도 웨이브 마커)
        Color(1.0, 0.35, 0.78, 0.9)
        Line(points=[start_x, start_y, end_x, end_y], width=1.4)

        # 신호 강도(1~4)에 따라 선 중간에 파동 tick 표시
        active_ticks = max(1, bars)
        for t_idx in range(active_ticks):
            frac = 0.25 + 0.50 * (t_idx / max(1, active_ticks))
            mx = start_x + (end_x - start_x) * frac
            my = start_y + (end_y - start_y) * frac
            tick_len = 4.5 + t_idx * 1.2
            Color(*color)
            Line(
                points=[mx - px * tick_len, my - py * tick_len, mx + px * tick_len, my + py * tick_len],
                width=1.2,
            )

        # 화살촉(분홍 화살표 방향)
        head_len = 8.0
        ang = math.atan2(dy, dx)
        Color(1.0, 0.35, 0.78, 0.95)
        for side in (-1, 1):
            hx = end_x - head_len * math.cos(ang + side * 0.42)
            hy = end_y - head_len * math.sin(ang + side * 0.42)
            Line(points=[end_x, end_y, hx, hy], width=1.4)

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
        try:
            self.canvas.before.clear()
            w, h = float(self.width), float(self.height)
            x0, y0 = float(self.x), float(self.y)
            if w <= 60 or h <= 60 or len(self._nodes) < 4:
                return

            with self.canvas.before:
                Color(0.04, 0.06, 0.11, 1)
                Rectangle(pos=(x0, y0), size=(w, h))

                Color(0.09, 0.16, 0.26, 0.55)
                step_x = max(28.0, w / 10.0)
                step_y = max(24.0, h / 8.0)
                gx = x0 + step_x
                while gx < x0 + w:
                    Line(points=[gx, y0, gx, y0 + h], width=0.8)
                    gx += step_x
                gy = y0 + step_y
                while gy < y0 + h:
                    Line(points=[x0, gy, x0 + w, gy], width=0.8)
                    gy += step_y

                # 확장된 빨간 박스 프레임
                Color(0.95, 0.20, 0.25, 0.9)
                Line(rectangle=(x0 + 2, y0 + 2, w - 4, h - 4), width=1.5)

                ap_cx = x0 + w * 0.5
                ap_cy = y0 + h - 42
                self._draw_router_icon(ap_cx, ap_cy, active=self._connected)
                self._place_label(
                    0,
                    ap_cx - 80,
                    ap_cy - 25,
                    160,
                    16,
                    f"AP: {self._ap_name[:14]}",
                    color=(0.35, 0.95, 1.0, 1),
                )

                ph_w = min(156.0, max(112.0, w * 0.36))
                ph_h = max(106.0, h * 0.34)
                ph_x = x0 + (w - ph_w) * 0.5
                ph_y = y0 + (h - ph_h) * 0.44
                Color(0.0, 1.0, 0.62, 0.85)
                Line(rectangle=(ph_x, ph_y, ph_w, ph_h), width=1.6)

                node_w = min(102.0, max(82.0, w * 0.26))
                node_h = 36.0
                node_positions = [
                    (x0 + 10, y0 + h - 84),
                    (x0 + w - node_w - 10, y0 + h - 84),
                    (x0 + 10, y0 + 16),
                    (x0 + w - node_w - 10, y0 + 16),
                ]
                # 분홍 화살표 방향 기준 출발점(상단 노드는 AP 하단, 하단 노드는 단말 하단 중앙)
                signal_origins = [
                    (ap_cx - 12, ap_cy - 10),
                    (ap_cx + 12, ap_cy - 10),
                    (ph_x + ph_w * 0.25, ph_y),
                    (ph_x + ph_w * 0.75, ph_y),
                ]
                inner_pts = [
                    (ph_x + 24, ph_y + ph_h - 20),
                    (ph_x + ph_w - 24, ph_y + ph_h - 20),
                    (ph_x + 24, ph_y + 20),
                    (ph_x + ph_w - 24, ph_y + 20),
                ]

                lbl_cursor = 1
                for i in range(4):
                    node = self._nodes[i]
                    nx, ny = node_positions[i]
                    ncx, ncy = nx + node_w * 0.5, ny + node_h * 0.5
                    ox, oy = signal_origins[i]

                    # 분홍 화살표 방향으로 신호선 생성
                    self._draw_directed_signal_line(ox, oy, ncx, ncy, node["bars"], node["color"])

                    Color(0.08, 0.12, 0.20, 0.96)
                    Rectangle(pos=(nx, ny), size=(node_w, node_h))
                    Color(*node["color"])
                    Line(rectangle=(nx, ny, node_w, node_h), width=1.3)

                    self._draw_signal_bar_meter(nx + 6, ny + 4, node["bars"], node["color"])

                    self._place_label(
                        lbl_cursor,
                        nx + 3,
                        ny + 14,
                        node_w - 6,
                        18,
                        f"#{node['idx']} {node['ssid'][:9]}",
                        color=(0.94, 0.98, 1.0, 1),
                    )
                    lbl_cursor += 1

                    ix, iy = inner_pts[i]
                    Color(*node["color"])
                    Line(circle=(ix, iy, 8), width=1.4)
                    self._place_label(
                        lbl_cursor,
                        ix - 7,
                        iy - 7,
                        14,
                        14,
                        str(node["idx"]),
                        color=(0.96, 0.98, 1.0, 1),
                    )
                    lbl_cursor += 1

                    if i % 2 == 0:
                        tag_x = x0 + 6
                        tag_align = "left"
                    else:
                        tag_x = x0 + w - 102
                        tag_align = "right"
                    tag_y = ph_y + ph_h * 0.54 if i < 2 else ph_y + 4

                    self._place_label(
                        lbl_cursor,
                        tag_x,
                        tag_y,
                        96,
                        32,
                        f"[{node['idx']}] {node['strength_txt']}\n{node['rssi_str']} | {node['speed']}",
                        halign=tag_align,
                        color=node["color"],
                    )
                    lbl_cursor += 1
        except Exception:
            pass


class WifiTrackerScreen(BoxLayout):
    SCAN_INTERVAL_SEC = 5.0

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=[10, 8, 10, 8], spacing=6, **kwargs)

        with self.canvas.before:
            Color(0.03, 0.05, 0.09, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        self._timer_event = None
        self._screen_active = True
        self._restricted_mode = False
        self._last_connected_state = None
        self._cached_nat_ip = "측정 불가"
        self._cached_nat_bssid = None

        self._step_count = 0
        self._pos_east_m = 0.0
        self._pos_north_m = 0.0
        self._heading_deg = 0.0

        # 1) 상태 및 5초 갱신 제어 바
        top_bar = BoxLayout(size_hint_y=None, height=38, spacing=8)
        self.status_label = make_label(
            text="상태: 스캔 대기중",
            size_hint_x=0.64,
            halign="left",
            valign="middle",
            font_size="11.5sp",
            color=(0.35, 0.95, 1.0, 1),
        )
        self.status_label.bind(size=lambda inst, val: setattr(inst, "text_size", (max(40, val[0] - 4), None)))

        self.toggle_btn = make_button(
            text="일시정지",
            size_hint_x=0.36,
            font_size="11.5sp",
            background_color=(0.08, 0.45, 0.62, 1),
        )
        self.toggle_btn.bind(on_press=self.toggle_scanning)

        top_bar.add_widget(self.status_label)
        top_bar.add_widget(self.toggle_btn)
        self.add_widget(top_bar)

        # 2) 보행 추정 / 방향 / 거리
        self.walk_label = make_label(
            text="이동추정: 걸음 0 | 위치: 측정 불가 | 방향: 측정 불가 | 거리: 측정 불가",
            size_hint_y=None,
            height=26,
            font_size="10.5sp",
            halign="left",
            valign="middle",
            color=(0.82, 0.92, 1.0, 1),
        )
        bind_auto_height(self.walk_label, min_height=26, pad_y=6, pad_x=4)
        self.add_widget(self.walk_label)

        # 3) 확장된 빨간 박스(AP 맵 + 분홍 화살표 방향 신호선)
        self.radar_widget = CyberApRadarWidget()
        self.add_widget(self.radar_widget)

        # 4) 하단 초록 박스 영역(노란 박스 쪽으로 배치 및 항목별 일렬 정렬)
        self.green_panel = BoxLayout(
            orientation="vertical",
            padding=[10, 8, 10, 8],
            spacing=6,
        )
        with self.green_panel.canvas.before:
            Color(0.05, 0.14, 0.10, 0.75)
            self._green_bg = Rectangle(pos=self.green_panel.pos, size=self.green_panel.size)
            Color(0.15, 0.85, 0.42, 0.9)
            self._green_border = Line(
                rectangle=(
                    self.green_panel.x + 2,
                    self.green_panel.y + 2,
                    max(1, self.green_panel.width - 4),
                    max(1, self.green_panel.height - 4),
                ),
                width=1.4,
            )
        self.green_panel.bind(pos=self._sync_green_panel, size=self._sync_green_panel)

        self.conn_detail_label = make_label(
            text=(
                "NAT IP: 측정 불가\n"
                "IPv4: 측정 불가\n"
                "서브넷: 측정 불가\n"
                "게이트웨이: 측정 불가\n"
                "DNS: 측정 불가\n"
                "TX 속도: 측정 불가  /  RX 속도: 측정 불가\n"
                "대역/채널폭: 측정 불가"
            ),
            size_hint_y=None,
            height=130,
            font_size="10.5sp",
            halign="left",
            valign="top",
            color=(0.88, 0.98, 0.92, 1),
        )
        bind_auto_height(self.conn_detail_label, min_height=120, pad_y=8, pad_x=4)
        self.green_panel.add_widget(self.conn_detail_label)

        self.list_header = make_label(
            text="주변 AP 스캔 목록 (상위 10개 - 스캔 대기중)",
            size_hint_y=None,
            height=26,
            font_size="11sp",
            halign="left",
            valign="middle",
            color=(0.0, 1.0, 0.62, 1),
        )
        bind_auto_height(self.list_header, min_height=26, pad_y=6, pad_x=4)
        self.green_panel.add_widget(self.list_header)

        self.ap_list_label = make_label(
            text="스캔 대기중",
            size_hint_y=None,
            font_size="10.5sp",
            halign="left",
            valign="top",
            color=(0.90, 0.97, 0.93, 1),
        )
        bind_auto_height(self.ap_list_label, min_height=80, pad_y=10, pad_x=4)

        scroll = ScrollView()
        scroll.add_widget(self.ap_list_label)
        self.green_panel.add_widget(scroll)

        self.add_widget(self.green_panel)

        if platform == "android":
            Clock.schedule_once(lambda dt: self.init_android_permissions(), 0.8)
        else:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드)"

        Clock.schedule_once(lambda dt: self.start_loop(), 1.0)

    def _sync_bg(self, *args):
        try:
            self._bg.pos = self.pos
            self._bg.size = self.size
        except Exception:
            pass

    def _sync_green_panel(self, *args):
        try:
            self._green_bg.pos = self.green_panel.pos
            self._green_bg.size = self.green_panel.size
            self._green_border.rectangle = (
                self.green_panel.x + 2,
                self.green_panel.y + 2,
                max(1, self.green_panel.width - 4),
                max(1, self.green_panel.height - 4),
            )
        except Exception:
            pass

    def init_android_permissions(self):
        try:
            from android.permissions import Permission, check_permission, request_permissions

            perms = [
                Permission.ACCESS_FINE_LOCATION,
                Permission.ACCESS_COARSE_LOCATION,
                Permission.ACCESS_WIFI_STATE,
                Permission.CHANGE_WIFI_STATE,
            ]
            has_loc = check_permission(Permission.ACCESS_FINE_LOCATION) or check_permission(
                Permission.ACCESS_COARSE_LOCATION
            )
            if has_loc:
                self._restricted_mode = False
                self.refresh_once()
            else:
                request_permissions(perms, self._on_permission_result)
        except Exception:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드)"

    def _on_permission_result(self, permissions, grant_results):
        try:
            granted = any(grant_results) if grant_results else False
            self._restricted_mode = not granted
            if self._restricted_mode:
                self.status_label.text = "상태: 스캔 대기중 (권한 거부 - 제한 모드)"
            else:
                self.status_label.text = "상태: 스캔 대기중"
            self.refresh_once()
        except Exception:
            pass

    def toggle_scanning(self, instance):
        try:
            if self._timer_event is None:
                self._screen_active = True
                self.start_loop()
                self.toggle_btn.text = "일시정지"
                self.refresh_once()
            else:
                self.stop_loop()
                self.toggle_btn.text = "5초 갱신 시작"
                self.status_label.text = "상태: 스캔 대기중"
        except Exception:
            pass

    def start_loop(self):
        try:
            if not self._screen_active:
                return
            if self._timer_event is None:
                self._timer_event = Clock.schedule_interval(lambda dt: self.refresh_once(), self.SCAN_INTERVAL_SEC)
                self.toggle_btn.text = "일시정지"
                Clock.schedule_once(lambda dt: self.refresh_once(), 0.2)
        except Exception:
            pass

    def stop_loop(self):
        try:
            if self._timer_event is not None:
                self._timer_event.cancel()
                self._timer_event = None
        except Exception:
            pass

    def on_screen_off(self):
        self._screen_active = False
        self.stop_loop()
        self.status_label.text = "상태: 스캔 대기중"

    def on_screen_on(self):
        self._screen_active = True
        self.start_loop()

    def _get_android_wifi_and_network_info(self):
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")

        activity = getattr(PythonActivity, "mActivity", None)
        if activity is None:
            raise RuntimeError("Activity not ready")

        wifi_mgr = activity.getSystemService(Context.WIFI_SERVICE)
        conn_mgr = activity.getSystemService(Context.CONNECTIVITY_SERVICE)

        connected = False
        ssid = "측정 불가"
        bssid = None
        rssi = None
        freq = None
        ch_width = None
        tx_mbps = "측정 불가"
        rx_mbps = "측정 불가"
        ipv4 = "측정 불가"
        subnet = "측정 불가"
        gateway = "측정 불가"
        dns_str = "측정 불가"

        try:
            wifi_info = wifi_mgr.getConnectionInfo() if wifi_mgr is not None else None
            if wifi_info is not None:
                raw_ssid = wifi_info.getSSID()
                bssid = wifi_info.getBSSID()
                rssi = wifi_info.getRssi()
                freq = wifi_info.getFrequency()
                link_speed = wifi_info.getLinkSpeed()
                if link_speed and link_speed > 0:
                    tx_mbps = f"{link_speed} Mbps"
                    rx_mbps = f"{link_speed} Mbps"
                try:
                    tx_link = wifi_info.getTxLinkSpeedMbps()
                    if tx_link and tx_link > 0:
                        tx_mbps = f"{tx_link} Mbps"
                except Exception:
                    pass
                try:
                    rx_link = wifi_info.getRxLinkSpeedMbps()
                    if rx_link and rx_link > 0:
                        rx_mbps = f"{rx_link} Mbps"
                except Exception:
                    pass
                try:
                    ch_width = wifi_info.getChannelWidth()
                except Exception:
                    ch_width = None

                if raw_ssid and raw_ssid != "<unknown ssid>":
                    ssid = str(raw_ssid).strip('"')
                    connected = True
                elif bssid and bssid != "02:00:00:00:00:00" and (rssi is not None and rssi > -120):
                    ssid = "(연결 AP)"
                    connected = True

                if ipv4 == "측정 불가":
                    ipv4 = int_ip_to_str(wifi_info.getIpAddress())
        except Exception:
            pass

        try:
            active_net = conn_mgr.getActiveNetwork() if conn_mgr is not None else None
            if active_net is not None:
                lp = conn_mgr.getLinkProperties(active_net)
                if lp is not None:
                    link_addrs = lp.getLinkAddresses()
                    if link_addrs and link_addrs.size() > 0:
                        la = link_addrs.get(0)
                        addr_str = str(la.getAddress().getHostAddress())
                        prefix_len = la.getPrefixLength()
                        if addr_str and ":" not in addr_str:
                            ipv4 = addr_str
                            connected = True
                            if prefix_len and 0 <= prefix_len <= 32:
                                mask_int = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
                                subnet = socket.inet_ntoa(struct.pack("!I", mask_int))
                    routes = lp.getRoutes()
                    if routes:
                        for i in range(routes.size()):
                            rt = routes.get(i)
                            if rt.isDefaultRoute():
                                gw_addr = rt.getGateway()
                                if gw_addr is not None:
                                    gw_host = str(gw_addr.getHostAddress())
                                    if gw_host and ":" not in gw_host:
                                        gateway = gw_host
                                        break
                    dns_list = lp.getDnsServers()
                    if dns_list and dns_list.size() > 0:
                        dns_items = []
                        for i in range(min(2, dns_list.size())):
                            dh = str(dns_list.get(i).getHostAddress())
                            if dh and ":" not in dh:
                                dns_items.append(dh)
                        if dns_items:
                            dns_str = ", ".join(dns_items)
        except Exception:
            pass

        if connected:
            if bssid and bssid != self._cached_nat_bssid:
                self._cached_nat_bssid = bssid
                self._cached_nat_ip = self._fetch_nat_ip_once()
            elif self._cached_nat_ip == "측정 불가":
                self._cached_nat_ip = self._fetch_nat_ip_once()
        else:
            self._cached_nat_bssid = None
            self._cached_nat_ip = "측정 불가"

        ap_items = []
        if not self._restricted_mode and wifi_mgr is not None:
            try:
                try:
                    wifi_mgr.startScan()
                except Exception:
                    pass
                results = wifi_mgr.getScanResults()
                if results is not None:
                    for i in range(results.size()):
                        item = results.get(i)
                        ap_ssid = str(item.SSID) if item.SSID else "(숨김 SSID)"
                        ap_level = item.level
                        ap_freq = item.frequency
                        ap_width_enum = None
                        try:
                            ap_width_enum = item.channelWidth
                        except Exception:
                            pass
                        band, width_str = freq_to_band_and_width(ap_freq, ap_width_enum)
                        dist_str = estimate_approx_distance_meters(ap_level, ap_freq)
                        ap_items.append(
                            {
                                "ssid": ap_ssid,
                                "rssi": ap_level if ap_level is not None else -999,
                                "rssi_str": f"{ap_level} dBm" if ap_level is not None else "측정 불가",
                                "band": band,
                                "width": width_str,
                                "dist_str": dist_str,
                                "link_speed_str": tx_mbps if connected else "측정 불가",
                            }
                        )
            except Exception:
                pass

        ap_items.sort(key=lambda x: x["rssi"], reverse=True)
        top10 = ap_items[:10]

        band, width_str = freq_to_band_and_width(freq, ch_width)
        dist_connected = estimate_approx_distance_meters(rssi, freq) if connected else "측정 불가"
        dir_str = azimuth_to_cardinal(self._heading_deg) if connected else "측정 불가"

        return {
            "connected": connected,
            "ssid": ssid,
            "band_width": f"{band} / {width_str}" if connected else "측정 불가",
            "tx_mbps": tx_mbps if connected else "측정 불가",
            "rx_mbps": rx_mbps if connected else "측정 불가",
            "nat_ip": self._cached_nat_ip if connected else "측정 불가",
            "ipv4": ipv4 if connected else "측정 불가",
            "subnet": subnet if connected else "측정 불가",
            "gateway": gateway if connected else "측정 불가",
            "dns": dns_str if connected else "측정 불가",
            "direction": dir_str,
            "distance": dist_connected,
            "top10": top10,
        }

    def _fetch_nat_ip_once(self):
        try:
            import urllib.request

            req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "WiFiTrackerS10/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                ip_text = resp.read().decode("utf-8").strip()
                if ip_text:
                    return ip_text
        except Exception:
            pass
        return "측정 불가"

    def _transition_connection_status(self, connected):
        if self._last_connected_state is None:
            self._last_connected_state = connected
            self.status_label.text = "상태: 스캔 중 (연결됨)" if connected else "상태: 연결끊김 (스캔 대기중)"
            return

        if self._last_connected_state and not connected:
            self._last_connected_state = False
            self.status_label.text = "상태: 연결끊김"
            Clock.schedule_once(lambda dt: self._set_reconnecting(), 0.7)
        elif not self._last_connected_state and connected:
            self._last_connected_state = True
            self.status_label.text = "상태: 재연결중..."
            Clock.schedule_once(lambda dt: setattr(self.status_label, "text", "상태: 스캔 중 (연결됨)"), 0.8)
        else:
            if connected:
                mode_tag = " [제한 모드]" if self._restricted_mode else ""
                self.status_label.text = f"상태: 스캔 중{mode_tag}"
            else:
                self.status_label.text = "상태: 연결끊김 / 재연결중"

    def _set_reconnecting(self):
        if not self._last_connected_state and self._screen_active:
            self.status_label.text = "상태: 재연결중..."

    def refresh_once(self):
        if not self._screen_active:
            self.status_label.text = "상태: 스캔 대기중"
            return

        if platform != "android":
            self._transition_connection_status(False)
            self.radar_widget.update_radar(False, "측정 불가", [], "측정 불가")
            self.ap_list_label.text = "측정 불가"
            return

        try:
            data = self._get_android_wifi_and_network_info()
            self._transition_connection_status(data["connected"])

            pos_text = (
                f"동/서 {self._pos_east_m:+.1f}m, 남/북 {self._pos_north_m:+.1f}m"
                if not self._restricted_mode and self._step_count > 0
                else "측정 불가"
            )
            self.walk_label.text = (
                f"이동추정: 걸음 {self._step_count} | 위치: {pos_text} | "
                f"방향: {data['direction']} | 거리: {data['distance']}"
            )

            self.conn_detail_label.text = (
                f"NAT IP: {data['nat_ip']}\n"
                f"IPv4: {data['ipv4']}\n"
                f"서브넷: {data['subnet']}\n"
                f"게이트웨이: {data['gateway']}\n"
                f"DNS: {data['dns']}\n"
                f"TX 속도: {data['tx_mbps']}  /  RX 속도: {data['rx_mbps']}\n"
                f"대역/채널폭: {data['band_width']}"
            )

            self.radar_widget.update_radar(
                data["connected"],
                data["ssid"],
                data["top10"],
                data["tx_mbps"],
            )

            if self._restricted_mode:
                self.list_header.text = "주변 AP 스캔 목록 (위치 권한 거부 - 제한 모드)"
                self.ap_list_label.text = "위치 권한 미허용으로 주변 AP 상세 목록이 제한됩니다 (측정 불가)."
            elif not data["top10"]:
                self.list_header.text = "주변 AP 스캔 목록 (상위 10개)"
                self.ap_list_label.text = "측정 불가"
            else:
                self.list_header.text = f"주변 AP 스캔 목록 (상위 {len(data['top10'])}개 - 5초 갱신)"
                lines = []
                for idx, ap in enumerate(data["top10"], start=1):
                    lines.append(
                        f"{idx}. {ap['ssid']} | {ap['rssi_str']} | {ap['band']} ({ap['width']}) | {ap['dist_str']}"
                    )
                self.ap_list_label.text = "\n".join(lines)

        except Exception:
            traceback.print_exc()
            self.status_label.text = "상태: 스캔 대기중"
            self.ap_list_label.text = "측정 불가"


class WifiTrackerS10App(App):
    def build(self):
        self.title = "WiFi Tracker S10"
        self.screen = WifiTrackerScreen()
        return self.screen

    def on_pause(self):
        if hasattr(self, "screen"):
            self.screen.on_screen_off()
        return True

    def on_resume(self):
        if hasattr(self, "screen"):
            self.screen.on_screen_on()


if __name__ == "__main__":
    WifiTrackerS10App().run()
