import math
import os
import socket
import struct

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
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
    """글자 길이에 맞춰 높이와 줄바꿈 폭을 자동 조절해 잘림 방지"""

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


def int_ip_to_str(ip_int):
    if not ip_int or ip_int == 0:
        return "측정 불가"
    try:
        return socket.inet_ntoa(struct.pack("<I", ip_int & 0xFFFFFFFF))
    except Exception:
        return "측정 불가"


class SignalSummaryCard(BoxLayout):
    """상단 요약 카드(여백을 충분히 확보해 글자 잘림/겹침 방지)"""

    def __init__(self, **kwargs):
        super().__init__(
            orientation="vertical",
            padding=[10, 8, 10, 8],
            spacing=6,
            size_hint_y=None,
            height=136,
            **kwargs,
        )
        with self.canvas.before:
            Color(0.11, 0.14, 0.18, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(0.20, 0.75, 0.38, 0.9)
            self._border = Line(
                rectangle=(self.x + 2, self.y + 2, max(1, self.width - 4), max(1, self.height - 4)),
                width=1.4,
            )

        self.bind(pos=self._update_canvas, size=self._update_canvas)

        self.ap_banner = make_label(
            text="AP: 측정 불가 | 방향: 측정 불가 | 거리: 측정 불가",
            size_hint_y=None,
            height=24,
            font_size="10.5sp",
            halign="left",
            valign="middle",
            color=(0.96, 0.98, 1, 1),
        )
        bind_auto_height(self.ap_banner, min_height=24, pad_y=6, pad_x=4)
        self.add_widget(self.ap_banner)

        self.quad_grid = GridLayout(cols=2, rows=2, spacing=[10, 6])
        self.quad_labels = []
        for idx in range(4):
            lbl = make_label(
                text=f"[{idx + 1}] 강도: 측정 불가\n속도: 측정 불가",
                font_size="10.5sp",
                halign="left",
                valign="middle",
                color=(0.86, 0.96, 0.89, 1),
            )
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", (max(40, val[0] - 4), None)))
            self.quad_labels.append(lbl)
            self.quad_grid.add_widget(lbl)
        self.add_widget(self.quad_grid)

    def _update_canvas(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rectangle = (self.x + 2, self.y + 2, max(1, self.width - 4), max(1, self.height - 4))

    def update_summary(self, connected_ssid, direction_str, dist_str, top_aps, default_speed="측정 불가"):
        ap_name = connected_ssid if connected_ssid else "측정 불가"
        self.ap_banner.text = f"AP: {ap_name} | 방향: {direction_str} | 거리: {dist_str}"
        for i in range(4):
            if i < len(top_aps):
                ap = top_aps[i]
                rssi_str = ap.get("rssi_str", "측정 불가")
                speed_str = ap.get("link_speed_str") or default_speed or "측정 불가"
                self.quad_labels[i].text = f"[{i + 1}] 강도: {rssi_str}\n속도: {speed_str}"
            else:
                self.quad_labels[i].text = f"[{i + 1}] 강도: 측정 불가\n속도: 측정 불가"


class WifiTrackerScreen(BoxLayout):
    SCAN_INTERVAL_SEC = 5.0

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=[10, 8, 10, 8], spacing=6, **kwargs)

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

        # 1) 상태 및 5초 갱신 버튼
        top_bar = BoxLayout(size_hint_y=None, height=40, spacing=8)
        self.status_label = make_label(
            text="상태: 스캔 대기중",
            size_hint_x=0.64,
            halign="left",
            valign="middle",
            font_size="11.5sp",
        )
        self.status_label.bind(size=lambda inst, val: setattr(inst, "text_size", (max(40, val[0] - 4), None)))

        self.toggle_btn = make_button(text="일시정지", size_hint_x=0.36, font_size="11.5sp")
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
        )
        bind_auto_height(self.walk_label, min_height=26, pad_y=6, pad_x=4)
        self.add_widget(self.walk_label)

        # 3) 요약 카드
        self.summary_card = SignalSummaryCard()
        self.add_widget(self.summary_card)

        # 4) NAT IP / IPv4·서브넷·게이트웨이·DNS / TX·RX 속도 / 대역·채널폭
        self.conn_detail_label = make_label(
            text=(
                "NAT IP: 측정 불가\n"
                "IPv4: 측정 불가 | 서브넷: 측정 불가 | 게이트웨이: 측정 불가 | DNS: 측정 불가\n"
                "TX 속도: 측정 불가  /  RX 속도: 측정 불가\n"
                "대역/채널폭: 측정 불가"
            ),
            size_hint_y=None,
            height=96,
            font_size="10.5sp",
            halign="left",
            valign="top",
        )
        bind_auto_height(self.conn_detail_label, min_height=92, pad_y=10, pad_x=4)
        self.add_widget(self.conn_detail_label)

        # 5) 주변 AP 스캔 목록 헤더 (여백을 넉넉히 주어 잘림 방지)
        self.list_header = make_label(
            text="주변 AP 스캔 목록 (상위 10개 - 스캔 대기중)",
            size_hint_y=None,
            height=28,
            font_size="11sp",
            halign="left",
            valign="middle",
        )
        bind_auto_height(self.list_header, min_height=28, pad_y=8, pad_x=4)
        self.add_widget(self.list_header)

        # 6) 주변 AP 스캔 목록 본문
        self.ap_list_label = make_label(
            text="스캔 대기중",
            size_hint_y=None,
            font_size="10.5sp",
            halign="left",
            valign="top",
        )
        bind_auto_height(self.ap_list_label, min_height=80, pad_y=12, pad_x=4)

        scroll = ScrollView()
        scroll.add_widget(self.ap_list_label)
        self.add_widget(scroll)

        if platform == "android":
            Clock.schedule_once(lambda dt: self.init_android_permissions(), 0.4)
        else:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드)"

        self.start_loop()

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
            else:
                request_permissions(perms, self._on_permission_result)
        except Exception:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드)"

    def _on_permission_result(self, permissions, grant_results):
        granted = any(grant_results) if grant_results else False
        self._restricted_mode = not granted
        if self._restricted_mode:
            self.status_label.text = "상태: 스캔 대기중 (권한 거부 - 제한 모드)"
        else:
            self.status_label.text = "상태: 스캔 대기중"
        self.refresh_once()

    def toggle_scanning(self, instance):
        if self._timer_event is None:
            self._screen_active = True
            self.start_loop()
            self.toggle_btn.text = "일시정지"
            self.refresh_once()
        else:
            self.stop_loop()
            self.toggle_btn.text = "5초 갱신 시작"
            self.status_label.text = "상태: 스캔 대기중"

    def start_loop(self):
        if not self._screen_active:
            return
        if self._timer_event is None:
            self._timer_event = Clock.schedule_interval(lambda dt: self.refresh_once(), self.SCAN_INTERVAL_SEC)
            self.toggle_btn.text = "일시정지"

    def stop_loop(self):
        if self._timer_event is not None:
            self._timer_event.cancel()
            self._timer_event = None

    def on_screen_off(self):
        self._screen_active = False
        self.stop_loop()
        self.status_label.text = "상태: 스캔 대기중"

    def on_screen_on(self):
        self._screen_active = True
        self.start_loop()
        self.refresh_once()

    def _get_android_wifi_and_network_info(self):
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")

        activity = PythonActivity.mActivity
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
            wifi_info = wifi_mgr.getConnectionInfo()
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
                    ssid = raw_ssid.strip('"')
                    connected = True
                elif bssid and bssid != "02:00:00:00:00:00" and (rssi is not None and rssi > -120):
                    ssid = "(연결 AP)"
                    connected = True

                if ipv4 == "측정 불가":
                    ipv4 = int_ip_to_str(wifi_info.getIpAddress())
        except Exception:
            pass

        try:
            active_net = conn_mgr.getActiveNetwork()
            if active_net is not None:
                lp = conn_mgr.getLinkProperties(active_net)
                if lp is not None:
                    link_addrs = lp.getLinkAddresses()
                    if link_addrs and link_addrs.size() > 0:
                        la = link_addrs.get(0)
                        addr_str = la.getAddress().getHostAddress()
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
                                    gw_host = gw_addr.getHostAddress()
                                    if gw_host and ":" not in gw_host:
                                        gateway = gw_host
                                        break
                    dns_list = lp.getDnsServers()
                    if dns_list and dns_list.size() > 0:
                        dns_items = []
                        for i in range(min(2, dns_list.size())):
                            dh = dns_list.get(i).getHostAddress()
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
        if not self._restricted_mode:
            try:
                try:
                    wifi_mgr.startScan()
                except Exception:
                    pass
                results = wifi_mgr.getScanResults()
                if results is not None:
                    for i in range(results.size()):
                        item = results.get(i)
                        ap_ssid = item.SSID if item.SSID else "(숨김 SSID)"
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
            self.summary_card.update_summary("측정 불가", "측정 불가", "측정 불가", [], "측정 불가")
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
                f"IPv4: {data['ipv4']} | 서브넷: {data['subnet']} | 게이트웨이: {data['gateway']} | DNS: {data['dns']}\n"
                f"TX 속도: {data['tx_mbps']}  /  RX 속도: {data['rx_mbps']}\n"
                f"대역/채널폭: {data['band_width']}"
            )

            self.summary_card.update_summary(
                data["ssid"] if data["connected"] else "측정 불가",
                data["direction"],
                data["distance"],
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
