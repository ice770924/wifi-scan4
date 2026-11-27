from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform


class WifiTrackerLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=16, spacing=12, **kwargs)

        self.status_label = Label(
            text="WiFi Tracker S10 Ready",
            size_hint_y=None,
            height=48,
        )
        self.add_widget(self.status_label)

        btn_bar = BoxLayout(size_hint_y=None, height=52, spacing=10)
        scan_btn = Button(text="Scan WiFi")
        scan_btn.bind(on_press=self.start_scan)
        auto_btn = Button(text="Auto Scan (3s)")
        auto_btn.bind(on_press=self.toggle_auto_scan)

        btn_bar.add_widget(scan_btn)
        btn_bar.add_widget(auto_btn)
        self.add_widget(btn_bar)

        self.result_label = Label(
            text="Tap 'Scan WiFi' to list nearby access points.",
            size_hint_y=None,
            halign="left",
            valign="top",
        )
        self.result_label.bind(
            texture_size=lambda inst, val: setattr(inst, "height", val[1] + 20),
            width=lambda inst, val: setattr(inst, "text_size", (val, None)),
        )

        scroll = ScrollView()
        scroll.add_widget(self.result_label)
        self.add_widget(scroll)

        self._auto_event = None
        if platform == "android":
            Clock.schedule_once(lambda dt: self.request_android_permissions(), 0.5)

    def request_android_permissions(self):
        try:
            from android.permissions import Permission, request_permissions

            request_permissions(
                [
                    Permission.ACCESS_FINE_LOCATION,
                    Permission.ACCESS_COARSE_LOCATION,
                    Permission.ACCESS_WIFI_STATE,
                    Permission.CHANGE_WIFI_STATE,
                ]
            )
        except Exception as e:
            self.status_label.text = f"Permission note: {e}"

    def toggle_auto_scan(self, instance):
        if self._auto_event is None:
            self._auto_event = Clock.schedule_interval(lambda dt: self.scan_wifi(), 3.0)
            instance.text = "Stop Auto Scan"
            self.scan_wifi()
        else:
            self._auto_event.cancel()
            self._auto_event = None
            instance.text = "Auto Scan (3s)"

    def start_scan(self, instance):
        self.scan_wifi()

    def scan_wifi(self):
        if platform != "android":
            self.status_label.text = "Desktop Preview Mode"
            self.result_label.text = (
                "Run on Galaxy S10 (Android) to view SSID, signal (dBm), frequency (MHz), and BSSID."
            )
            return

        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")

            activity = PythonActivity.mActivity
            wifi_manager = activity.getSystemService(Context.WIFI_SERVICE)
            try:
                wifi_manager.startScan()
            except Exception:
                pass
            results = wifi_manager.getScanResults()

            lines = []
            if results is not None:
                for i in range(results.size()):
                    item = results.get(i)
                    ssid = item.SSID or "(Hidden SSID)"
                    bssid = item.BSSID or ""
                    level = item.level
                    freq = item.frequency
                    lines.append(f"{ssid} | {level} dBm | {freq} MHz | {bssid}")

            self.status_label.text = f"Scan complete: {len(lines)} AP(s)"
            self.result_label.text = "\n".join(lines) if lines else "No WiFi scan results available."
        except Exception as e:
            self.status_label.text = "Scan error"
            self.result_label.text = str(e)


class WifiTrackerApp(App):
    def build(self):
        self.title = "WiFi Tracker S10"
        return WifiTrackerLayout()


if __name__ == "__main__":
    WifiTrackerApp().run()
