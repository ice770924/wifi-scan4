import math
import socket
import struct
import time

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
# 1. 실내/실외 2D 맵 및 히트맵 시각화 (Canvas 기반 AP 좌표·신호 등고선 표시)
# 2. 걸음(PDR) + 지자기/자이로 센서 퓨전 정밀화 (스텝 길이 보정 및 실내 벽 감쇠 모델)
# 3. 802.11ax/be (Wi-Fi 6/6E/7) 채널·링크 대역폭 정밀 파싱 및 SNR/재전송률 로깅
# 4. 스캔 이력 CSV/JSON 내보내기 및 특정 SSID/BSSID 추적 알람 기능
# ==============================================================================


def register_korean_font():
    """안드로이드/데스크톱에서 한글 깨짐 방지를 위한 폰트 등록"""
    candidates = [
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/NanumGothic.ttf",
        "/system/fonts/DroidSansFallback.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            import os

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


def freq_to_band_and_width(freq_mhz, channel_width_enum=None):
    """주파수(MHz)를 2.4 / 5 / 6 GHz 대역 및 채널 폭(MHz)으로 변환"""
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
    """자유공간 경로손실(FSPL) 기반 근사 거리 추정 -> '약 X.Xm' 문자열 반환"""
    if rssi_dbm is None or rssi_dbm >= 0 or not freq_mhz or freq_mhz <= 0:
        return "측정 불가"
    try:
        # 1m 기준 대략적 참조 손실 보정
        fspl_1m = 20 * math.log10(freq_mhz) + 20 * math.log10(1.0) - 27.55
        exp = (abs(fspl_1m) - abs(rssi_dbm)) / (10.0 * 2.7)
        dist = max(0.5, min(150.0, math.pow(10.0, exp)))
        return f"약 {dist:.1f}m"
    except Exception:
        return "측정 불가"


def azimuth_to_cardinal(deg):
    """방위각(0~360)을 동서남북 방향으로 변환"""
    if deg is None:
        return "측정 불가"
    dirs = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
    idx = int(((deg % 360) + 22.5) // 45) % 8
    return dirs[idx]


def int_ip_to_str(ip_int):
    """Android WifiInfo ipAddress 정수를 IPv4 문자열로 변환"""
    if not ip_int or ip_int == 0:
        return "측정 불가"
    try:
        return socket.inet_ntoa(struct.pack("<I", ip_int & 0xFFFFFFFF))
    except Exception:
        return "측정 불가"


class RadarPreviewWidget(BoxLayout):
    """그림 구도(상단 AP/연결 신호 및 4개 구역 강도/속도)를 한 화면에 시각화"""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=8, spacing=4, size_hint_y=None, height=130, **kwargs)
        with self.canvas.before:
            Color(0.10, 0.12, 0.16, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(0.20, 0.75, 0.35, 0.85)
            self._border = Line(rectangle=(self.x + 4, self.y + 4, self.width - 8, self.height - 8), width=1.5)

        self.bind(pos=self._update_canvas, size=self._update_canvas)

        self.ap_banner = make_label(
            text="AP: 스캔 대기중 | 방향: 측정 불가 | 거리: 측정 불가",
            size_hint_y=None,
            height=24,
            font_size="12sp",
            color=(0.95, 0.97, 1, 1),
        )
        self.add_widget(self.ap_banner)

        self.quad_grid = GridLayout(cols=2, rows=2, spacing=4)
        self.quad_labels = []
        for idx in range(4):
            lbl = make_label(
                text=f"[{idx + 1}] 강도: 측정 불가\n연결속도: 측정 불가",
                font_size="11sp",
                halign="left",
                valign="middle",
                color=(0.85, 0.95, 0.88, 1),
            )
            lbl.bind(size=lambda inst, val: setattr(inst, "text_size", (val[0] - 6, val[1])))
            self.quad_labels.append(lbl)
            self.quad_grid.add_widget(lbl)
        self.add_widget(self.quad_grid)

    def _update_canvas(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rectangle = (self.x + 4, self.y + 4, max(1, self.width - 8), max(1, self.height - 8))

    def update_summary(self, connected_ssid, direction_str, dist_str, top_aps):
        ap_name = connected_ssid if connected_ssid else "스캔 대기중"
        self.ap_banner.text = f"AP: {ap_name} | 방향: {direction_str} | 거리: {dist_str}"
        for i in range(4):
            if i < len(top_aps):
                ap = top_aps[i]
                rssi = ap.get("rssi_str", "측정 불가")
                speed = ap.get("link_speed_str", "측정 불가")
                ssid = ap.get("ssid", "측정 불가")
                self.quad_labels[i].text = f"[{i + 1}] {ssid}\n강도: {rssi} | 속도: {speed}"
            else:
                self.quad_labels[i].text = f"[{i + 1}] 강도: 측정 불가\n연결속도: 측정 불가"


class WifiTrackerScreen(BoxLayout):
    SCAN_INTERVAL_SEC = 5.0

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=10, spacing=6, **kwargs)

        self._timer_event = None
        self._screen_active = True
        self._location_granted = False
        self._restricted_mode = False
        self._last_connected_state = None
        self._cached_nat_ip = "측정 불가"
        self._cached_nat_bssid = None

        # 보행 추정 상태(걸음 수 * 보폭 -> 동/서/남/북 누적 변위)
        self._step_count = 0
        self._last_step_time = 0.0
        self._pos_east_m = 0.0
        self._pos_north_m = 0.0
        self._heading_deg = 0.0

        # 1) 상단 상태 및 제어 바
        top_bar = BoxLayout(size_hint_y=None, height=40, spacing=8)
        self.status_label = make_label(
            text="상태: 스캔 대기중",
            size_hint_x=0.62,
            halign="left",
            valign="middle",
            font_size="13sp",
        )
        self.status_label.bind(size=lambda inst, val: setattr(inst, "text_size", val))

        self.toggle_btn = make_button(text="5초 갱신 시작", size_hint_x=0.38, font_size="12sp")
        self.toggle_btn.bind(on_press=self.toggle_scanning)

        top_bar.add_widget(self.status_label)
        top_bar.add_widget(self.toggle_btn)
        self.add_widget(top_bar)

        # 2) 보행 추정 / 방향 / 거리 / 연결 상태 요약 바
        self.walk_label = make_label(
            text="이동추정: 걸음 0 | 위치: 측정 불가 | 방향: 측정 불가 | 거리: 측정 불가",
            size_hint_y=None,
            height=34,
            font_size="11sp",
            halign="left",
            valign="middle",
        )
        self.walk_label.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(self.walk_label)

        # 3) 그림 구도 기반 요약 패널
        self.radar_widget = RadarPreviewWidget()
        self.add_widget(self.radar_widget)

        # 4) 연결 상세(NAT IP / IPv4·서브넷·게이트웨이·DNS / TX·RX 속도 / 대역·채널폭)
        self.conn_detail_label = make_label(
            text=(
                "NAT IP: 측정 불가\n"
                "IPv4: 측정 불가 | 서브넷: 측정 불가 | 게이트웨이: 측정 불가 | DNS: 측정 불가\n"
                "TX 속도: 측정 불가  /  RX 속도: 측정 불가\n"
                "대역/채널폭: 측정 불가"
            ),
            size_hint_y=None,
            height=84,
            font_size="11sp",
            halign="left",
            valign="top",
        )
        self.conn_detail_label.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(self.conn_detail_label)

        # 5) 주변 AP 스캔 목록 (상위 10개)
        self.list_header = make_label(
            text="주변 AP 스캔 목록 (상위 10개 - 스캔 대기중)",
            size_hint_y=None,
            height=24,
            font_size="12sp",
            halign="left",
            valign="middle",
        )
        self.list_header.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(self.list_header)

        self.ap_list_label = make_label(
            text="스캔 대기중",
            size_hint_y=None,
            font_size="11sp",
            halign="left",
            valign="top",
        )
        self.ap_list_label.bind(
            texture_size=lambda inst, val: setattr(inst, "height", max(80, val[1] + 12)),
            width=lambda inst, val: setattr(inst, "text_size", (val, None)),
        )

        scroll = ScrollView()
        scroll.add_widget(self.ap_list_label)
        self.add_widget(scroll)

        # 시작 시 권한 확인 및 5초 루프 시작
        if platform == "android":
            Clock.schedule_once(lambda dt: self.init_android_permissions(), 0.4)
        else:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드: 비안드로이드)"

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
                self._location_granted = True
                self._restricted_mode = False
            else:
                request_permissions(perms, self._on_permission_result)
        except Exception:
            self._restricted_mode = True
            self.status_label.text = "상태: 스캔 대기중 (제한 모드)"

    def _on_permission_result(self, permissions, grant_results):
        granted = any(grant_results) if grant_results else False
        self._location_granted = granted
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
        """화면이 꺼질 때 갱신 중단"""
        self._screen_active = False
        self.stop_loop()
        self.status_label.text = "상태: 스캔 대기중 (화면 꺼짐)"

    def on_screen_on(self):
        """화면이 켜질 때 갱신 재개"""
        self._screen_active = True
        self.start_loop()
        self.refresh_once()

    def _update_step_estimate(self):
        """보행 걸음 수 및 방향 추정 갱신 (센서 미지원/권한 제한 시 안전 처리)"""
        if self._restricted_mode or platform != "android":
            return
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")
            Sensor = autoclass("android.hardware.Sensor")
            SensorManager = autoclass("android.hardware.SensorManager")

            activity = PythonActivity.mActivity
            sm = activity.getSystemService(Context.SENSOR_SERVICE)
            step_sensor = sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)
            # TYPE_STEP_COUNTER는 부팅 이후 누적 걸음 수 제공
            if step_sensor is not None:
                # 간단한 폴링/보정 구조: 실제 운영 시 SensorEventListener 등록으로 확장 가능
                pass
        except Exception:
            pass

    def _get_android_wifi_and_network_info(self):
        """Android WifiManager / ConnectivityManager / LinkProperties 조회"""
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        BuildVersion = autoclass("android.os.Build$VERSION")

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
                elif bssid and bssid != "02:00:00:00:00:00":
                    ssid = "(숨김 SSID)"
                    connected = True

                if ipv4 == "측정 불가":
                    ip_int = wifi_info.getIpAddress()
                    ipv4 = int_ip_to_str(ip_int)
        except Exception:
            pass

        # LinkProperties로 IPv4 / 서브넷 / 게이트웨이 / DNS 추출
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

        # NAT IP: AP에 연결했을 때만 조회하고, 같은 BSSID 연결 중에는 재갱신하지 않음
        if connected:
            if bssid and bssid != self._cached_nat_bssid:
                self._cached_nat_bssid = bssid
                self._cached_nat_ip = self._fetch_nat_ip_once()
            elif self._cached_nat_ip == "측정 불가":
                self._cached_nat_ip = self._fetch_nat_ip_once()
        else:
            self._cached_nat_bssid = None
            self._cached_nat_ip = "측정 불가"

        # 주변 AP 스캔 목록
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
                        ap_bssid = item.BSSID if item.BSSID else "측정 불가"
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
                                "bssid": ap_bssid,
                                "rssi": ap_level if ap_level is not None else -999,
                                "rssi_str": f"{ap_level} dBm" if ap_level is not None else "측정 불가",
                                "freq": ap_freq,
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
            "rssi_str": f"{rssi} dBm" if (connected and rssi is not None) else "측정 불가",
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
        """외부 공인/NAT IP 1회 조회(실패 시 측정 불가)"""
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
        """네트워크 전환 시 '연결끊김' -> '재연결중' -> '연결됨' 부드러운 상태 전환"""
        if self._last_connected_state is None:
            self._last_connected_state = connected
            if connected:
                self.status_label.text = "상태: 스캔 중 (AP 연결됨)"
            else:
                self.status_label.text = "상태: 연결끊김 (스캔 대기중)"
            return

        if self._last_connected_state and not connected:
            self._last_connected_state = False
            self.status_label.text = "상태: 연결끊김"
            Clock.schedule_once(lambda dt: self._set_reconnecting_if_disconnected(), 0.7)
        elif not self._last_connected_state and connected:
            self._last_connected_state = True
            self.status_label.text = "상태: 재연결중..."
            Clock.schedule_once(lambda dt: setattr(self.status_label, "text", "상태: 스캔 중 (AP 연결됨)"), 0.8)
        else:
            if connected:
                mode_tag = " [제한 모드]" if self._restricted_mode else ""
                self.status_label.text = f"상태: 스캔 중{mode_tag}"
            else:
                self.status_label.text = "상태: 연결끊김 / 재연결중"

    def _set_reconnecting_if_disconnected(self):
        if not self._last_connected_state and self._screen_active:
            self.status_label.text = "상태: 재연결중..."

    def refresh_once(self):
        if not self._screen_active:
            self.status_label.text = "상태: 스캔 대기중"
            return

        self._update_step_estimate()

        if platform != "android":
            self._transition_connection_status(False)
            self.walk_label.text = (
                f"이동추정: 걸음 {self._step_count} | 위치: 측정 불가 | 방향: 측정 불가 | 거리: 측정 불가"
            )
            self.conn_detail_label.text = (
                "NAT IP: 측정 불가\n"
                "IPv4: 측정 불가 | 서브넷: 측정 불가 | 게이트웨이: 측정 불가 | DNS: 측정 불가\n"
                "TX 속도: 측정 불가  /  RX 속도: 측정 불가\n"
                "대역/채널폭: 측정 불가"
            )
            self.radar_widget.update_summary("측정 불가", "측정 불가", "측정 불가", [])
            self.ap_list_label.text = "측정 불가 (Android 갤럭시 S10 실행 시 스캔)"
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

            self.radar_widget.update_summary(
                data["ssid"] if data["connected"] else "측정 불가",
                data["direction"],
                data["distance"],
                data["top10"],
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
        """화면이 꺼지거나 앱이 백그라운드로 갈 때 갱신 중단"""
        if hasattr(self, "screen"):
            self.screen.on_screen_off()
        return True

    def on_resume(self):
        """화면이 켜지거나 앱으로 돌아올 때 갱신 재개"""
        if hasattr(self, "screen"):
            self.screen.on_screen_on()


if __name__ == "__main__":
    WifiTrackerS10App().run()
