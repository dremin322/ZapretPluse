from __future__ import annotations
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF, QSize, QByteArray
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QPainter, QPen, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QSystemTrayIcon,
    QMenu, QCheckBox, QComboBox, QDialog, QFormLayout, QDialogButtonBox, QSpinBox,
    QTabWidget, QTextEdit, QStackedWidget, QScrollArea, QSizePolicy, QColorDialog, QBoxLayout
)
from version import __version__
from core.paths import ROOT
from PySide6.QtSvg import QSvgRenderer
from ui.style import build_style, DEFAULT_ACCENT


class UpdateWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, updater, mode: str, release=None, parent=None):
        super().__init__(parent)
        self.updater = updater
        self.mode = mode
        self.release = release

    def run(self):
        try:
            if self.mode == "check": result = self.updater.check()
            elif self.mode == "auto": result = self.updater.auto_update_supported_components()
            elif self.mode == "zapret": result = self.updater.install_zapret(self.release)
            elif self.mode == "app": result = self.updater.stage_app_update(self.release)
            else: raise RuntimeError(f"Unknown update mode: {self.mode}")
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class EngineWorker(QThread):
    """Run potentially slow engine lifecycle operations without freezing Qt."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, mode: str, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mode = mode

    def run(self):
        try:
            if self.mode == "start":
                result = self.controller.start_all()
            elif self.mode == "stop":
                self.controller.stop_all()
                result = {"stopped": True}
            else:
                raise RuntimeError(f"Unknown engine mode: {self.mode}")
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))



class WatchdogWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            self.done.emit(self.controller.recovery_tick())
        except Exception as exc:
            self.failed.emit(str(exc))


class StrategyTuneWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            self.done.emit(self.controller.tune_strategy(force=True))
        except Exception as exc:
            self.failed.emit(str(exc))


ASSETS_DIR = ROOT / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
_UI_ACCENT = DEFAULT_ACCENT
_UI_NEUTRAL = "#9AA7B9"

def _svg_icon(name: str, color: str, size: int = 24) -> QIcon:
    path = ICONS_DIR / f"{name}.svg"
    if not path.exists():
        return QIcon()
    data = path.read_text(encoding="utf-8", errors="replace")
    # Lucide sources in this project use a fixed stroke color. Recolor at runtime
    # so the whole UI follows the user's accent color without shipping duplicates.
    import re
    data = re.sub(r'stroke="#[0-9A-Fa-f]{6}"', f'stroke="{color}"', data)
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    pm = QPixmap(size, size); pm.fill(Qt.transparent)
    painter = QPainter(pm); renderer.render(painter); painter.end()
    return QIcon(pm)

def ui_icon(name: str, active: bool = False, color: str | None = None, size: int = 24) -> QIcon:
    return _svg_icon(name, color or (_UI_ACCENT if active else _UI_NEUTRAL), size)

def set_icon(button: QPushButton, name: str, size: int = 19, active: bool = False, color: str | None = None):
    button.setIcon(ui_icon(name, active, color, size))
    button.setIconSize(QSize(size, size))

def card(name="card"):
    frame = QFrame(); frame.setObjectName(name)
    layout = QVBoxLayout(frame); layout.setContentsMargins(22, 20, 22, 20); layout.setSpacing(11)
    return frame, layout


class TrafficDonut(QWidget):
    """Compact session traffic donut with a quiet two-tone ring."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.up = 0
        self.down = 0
        self.setFixedSize(128, 128)

    def set_values(self, up: int, down: int):
        up, down = max(0, int(up)), max(0, int(down))
        if (up, down) == (self.up, self.down):
            return
        self.up, self.down = up, down
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(13, 13, 102, 102)
        # Quiet neutral track. The actual traffic segments below carry the contrast.
        dark = self.property("theme") != "light"
        track = QColor("#27313C" if dark else "#E8EDF4")
        p.setPen(QPen(track, 12, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(rect, 0, 360 * 16)
        total = self.up + self.down
        if total:
            # Flat ends + a tiny visual gap: the two colors never overlap and the
            # split looks crisp even when one traffic direction is very small.
            full = 360 * 16
            gap = 3 * 16 if self.up and self.down else 0
            usable = full - (gap * 2)
            up_span = int(usable * self.up / total) if self.up else 0
            down_span = usable - up_span if self.down else 0
            start = 90 * 16
            if up_span:
                p.setPen(QPen(QColor("#31C58A"), 12, Qt.SolidLine, Qt.FlatCap))
                p.drawArc(rect, start, -up_span)
                start -= up_span + gap
            if down_span:
                # Deliberate theme inversion: black in the light theme, white in dark.
                # This stays readable even when the user chooses a very dark accent color.
                down_color = "#FFFFFF" if dark else "#101419"
                p.setPen(QPen(QColor(down_color), 12, Qt.SolidLine, Qt.FlatCap))
                p.drawArc(rect, start, -down_span)
        text_color = QColor("#F4F7FB") if self.property("theme") != "light" else QColor("#172033")
        p.setPen(text_color)
        font = p.font(); font.setPointSize(16); font.setBold(True); p.setFont(font)
        p.drawText(QRectF(18, 39, 92, 35), Qt.AlignCenter, f"{total/1024:.1f}")
        font.setPointSize(9); font.setBold(False); p.setFont(font); p.setPen(QColor("#8793A5"))
        p.drawText(QRectF(18, 70, 92, 24), Qt.AlignCenter, "КБ")


class MainWindow(QMainWindow):
    NAV = [
        ("home", "house", "Главная"),
        ("strategy", "sliders-horizontal", "Стратегии"),
        ("sites", "list", "Сайты и списки"),
        ("telegram", "send", "Telegram Proxy"),
        ("updates", "download", "Обновления"),
        ("diagnostics", "activity", "Диагностика"),
    ]

    def __init__(self, controller, config):
        super().__init__()
        self.controller, self.config = controller, config
        # Protection uptime belongs to the active protection session, not to the GUI process.
        self._protection_started_at = None
        self._protection_started_wall = None
        self._last_any_on = False
        self._theme = (config.get("app", "theme", default="light") or "light") if config.get("app", "theme_user_selected", default=False) else "light"
        self._accent = config.get("app", "accent_color", default=DEFAULT_ACCENT) or DEFAULT_ACCENT
        if not config.get("app", "theme_user_selected", default=False):
            config.data.setdefault("app", {})["theme"] = "light"
            config.save()
        self.setWindowTitle("Zapret+")
        app_icon = ASSETS_DIR / "icon.ico"
        if app_icon.exists():
            self.setWindowIcon(QIcon(str(app_icon)))
        self.resize(980, 640); self.setMinimumSize(760, 520)
        self._update_worker = None; self._last_update_info = None
        self._engine_worker = None
        self._engine_operation = None
        self._busy_angle = 0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(70)
        self._busy_timer.timeout.connect(self._busy_tick)
        self._build(); self._apply_theme(self._theme); self._apply_responsive_layout(self.width()); self._center_on_screen()
        self.tray = None
        self.tray_toggle = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_status)
        self._watchdog_worker = None
        self._strategy_worker = None
        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(max(3000, int(config.get("app", "watchdog_interval", default=5) or 5) * 1000))
        self.watchdog_timer.timeout.connect(self._watchdog_tick)
        self._runtime_initialized = False

        # IMPORTANT: do not query engines/files/processes in __init__. A frozen Windows
        # build must be able to paint the complete interface before any potentially
        # blocking runtime work starts.
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.PointingHandCursor)

    def validate_ui(self) -> tuple[bool, str]:
        """Cheap structural check used before the window is presented."""
        central = self.centralWidget()
        problems = []
        if central is None:
            problems.append("centralWidget отсутствует")
        if not hasattr(self, "stack") or self.stack.count() < 6:
            problems.append("страницы интерфейса не созданы")
        if not hasattr(self, "sidebar"):
            problems.append("боковая панель не создана")
        if len(getattr(self, "nav_buttons", {})) != len(self.NAV):
            problems.append("кнопки навигации созданы не полностью")
        return (not problems, "; ".join(problems))

    def finish_initialization(self):
        """Run runtime/status work only after the first frame has been painted."""
        if self._runtime_initialized:
            return
        self._runtime_initialized = True
        try:
            self.refresh_sites()
            self.refresh_builtin()
            self.refresh_status()
            self.refresh_update_labels()
        finally:
            # Timers begin after initial UI exists. Individual worker operations remain async.
            self.timer.start()
            self.watchdog_timer.start()

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry(); frame = self.frameGeometry(); frame.moveCenter(geo.center()); self.move(frame.topLeft())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int):
        """Responsive breakpoints for small laptops, scaled Windows and narrow windows."""
        forced_compact = bool(self.config.get("app", "compact_sidebar", default=False))
        compact = forced_compact or width < 720
        if hasattr(self, "sidebar"):
            self.sidebar.setFixedWidth(72 if compact else 190)
            self.brand_name.setText("")
            self.brand_name.setObjectName("brand")
            for key, button in self.nav_buttons.items():
                button.setText("" if compact else self.nav_titles[key])
                button.setToolTip(self.nav_titles[key] if compact else "")
            self.settings_button.setText("" if compact else "Настройки")
            self.settings_button.setToolTip("Настройки" if compact else "")
            self.theme_button.setText("" if compact else "Тема")
            self.theme_button.setToolTip("Сменить тему" if compact else "")
            self.brand_name.style().unpolish(self.brand_name); self.brand_name.style().polish(self.brand_name)

        # Below ~860 px the right rail becomes a second section below the main cards.
        narrow = width < 860
        very_narrow = width < 790
        if hasattr(self, "home_top_layout"):
            self.home_top_layout.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self.home_cards_layout.setDirection(QBoxLayout.Direction.TopToBottom if very_narrow else QBoxLayout.Direction.LeftToRight)
            for widget in (self.stats_card, self.updates_home_card):
                widget.setMaximumWidth(16777215 if narrow else 278)
                widget.setMinimumWidth(0 if narrow else 238)
        if hasattr(self, "overall_hint"):
            self.overall_hint.setVisible(width >= 720)
        if hasattr(self, "footer_time"):
            self.footer_time.setVisible(width >= 820)

    def _build(self):
        root = QWidget(); root.setObjectName("appRoot")
        shell = QHBoxLayout(root); shell.setContentsMargins(0,0,0,0); shell.setSpacing(0)
        shell.addWidget(self._build_sidebar())
        self.stack = QStackedWidget(); self.stack.setObjectName("contentStack")
        self.pages = {}
        for key, builder in (("home", self._home_page), ("strategy", self._strategy_page), ("sites", self._sites_page), ("telegram", self._telegram_page), ("updates", self._updates_page), ("diagnostics", self._diagnostics_page)):
            page=builder(); self.pages[key]=self.stack.count(); self.stack.addWidget(page)
        shell.addWidget(self.stack,1); self.setCentralWidget(root)
        self._navigate("home")

    def _build_sidebar(self):
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(190)
        self.sidebar = side
        l = QVBoxLayout(side); l.setContentsMargins(16, 22, 16, 16); l.setSpacing(6)

        # Sidebar intentionally starts directly with navigation; application identity
        # remains in the Windows title/taskbar/tray rather than consuming sidebar space.
        self.brand_name = QLabel(""); self.brand_name.hide()
        l.addSpacing(2)

        self.nav_buttons = {}; self.nav_icons = {}; self.nav_titles = {}
        for key, icon_name, title in self.NAV:
            b = QPushButton(title); b.setObjectName("navButton"); b.setCheckable(True)
            set_icon(b, icon_name, 19); b.clicked.connect(lambda _=False, k=key: self._navigate(k))
            l.addWidget(b); self.nav_buttons[key] = b; self.nav_icons[key] = icon_name; self.nav_titles[key] = title
        l.addStretch(1)
        self.settings_button = QPushButton("Настройки"); self.settings_button.setObjectName("navButton"); set_icon(self.settings_button, "settings", 19); self.settings_button.clicked.connect(self.open_settings); l.addWidget(self.settings_button)
        self.theme_button = QPushButton("Тема"); self.theme_button.setObjectName("navButton"); set_icon(self.theme_button, "sun", 19); self.theme_button.clicked.connect(self.toggle_theme); l.addWidget(self.theme_button)
        return side

    def _page_shell(self, title, subtitle=""):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setObjectName("pageScroll")
        w=QWidget(); w.setObjectName("page"); l=QVBoxLayout(w); l.setContentsMargins(24,20,24,18); l.setSpacing(14)
        if title:
            h=QLabel(title); h.setObjectName("pageTitle"); l.addWidget(h)
        if subtitle:
            s=QLabel(subtitle); s.setObjectName("muted"); l.addWidget(s)
        scroll.setWidget(w); return scroll,w,l

    def _home_page(self):
        scroll, w, l = self._page_shell("", "")
        top = QHBoxLayout(); top.setSpacing(14); self.home_top_layout = top
        center = QVBoxLayout(); center.setSpacing(14); self.home_center_layout = center

        hero, hl = card("hero"); hl.setContentsMargins(22, 18, 22, 18)
        hr = QHBoxLayout(); hr.setSpacing(14)
        text = QVBoxLayout(); text.setSpacing(3)
        self.overall = QLabel("Защита выключена"); self.overall.setObjectName("headline")
        self.overall_hint = QLabel("Zapret и Telegram Proxy готовы к запуску"); self.overall_hint.setObjectName("muted")
        text.addWidget(self.overall); text.addWidget(self.overall_hint); hr.addLayout(text, 1)
        self.toggle = QPushButton("Включить всё"); self.toggle.setObjectName("power"); self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setMinimumWidth(148); set_icon(self.toggle, "power", 20, color="#FFFFFF")
        self.toggle.clicked.connect(self.toggle_all); hr.addWidget(self.toggle, 0, Qt.AlignVCenter)
        hl.addLayout(hr); center.addWidget(hero)

        cards = QHBoxLayout(); cards.setSpacing(12); self.home_cards_layout = cards
        self.zapret_home_card = self._zapret_card(); self.telegram_home_card = self._telegram_card()
        cards.addWidget(self.zapret_home_card, 1); cards.addWidget(self.telegram_home_card, 1); center.addLayout(cards)
        self.info = QLabel(""); self.info.setObjectName("statusInfo"); self.info.setWordWrap(True); center.addWidget(self.info)
        top.addLayout(center, 1)

        right = QVBoxLayout(); right.setSpacing(12); self.home_right_layout = right
        self.stats_card = self._stats_card(); self.updates_home_card = self._home_updates_card()
        right.addWidget(self.stats_card); right.addWidget(self.updates_home_card); right.addStretch(1); top.addLayout(right)
        l.addLayout(top, 1)

        footer = QFrame(); footer.setObjectName("footer"); fl = QHBoxLayout(footer); fl.setContentsMargins(14, 8, 14, 8)
        self.footer_state = QLabel("●  Система готова"); self.footer_state.setObjectName("ok"); self.footer_time = QLabel(""); self.footer_time.setObjectName("muted")
        fl.addWidget(self.footer_state); fl.addStretch(1); fl.addWidget(self.footer_time); l.addWidget(footer)
        return scroll

    def _zapret_card(self):
        c,l=card(); l.setContentsMargins(18,16,18,16); row=QHBoxLayout(); t=QLabel("Zapret"); t.setObjectName("section"); self.zapret_status=QLabel("○ Выключен"); row.addWidget(t); row.addStretch(1); row.addWidget(self.zapret_status); l.addLayout(row)
        l.addWidget(self._separator())
        a=QLabel("Стратегия"); a.setObjectName("muted"); l.addWidget(a); self.zapret_strategy=QLabel(self.config.get('zapret','strategy',default='general.bat')); self.zapret_strategy.setObjectName("value"); l.addWidget(self.zapret_strategy)
        b=QLabel("Game Filter"); b.setObjectName("muted"); l.addWidget(b); self.zapret_game=QLabel(self._game_label()); self.zapret_game.setObjectName("value"); l.addWidget(self.zapret_game)
        self.zapret_detail=QLabel(""); self.zapret_detail.setObjectName("muted"); self.zapret_detail.setWordWrap(True); l.addWidget(self.zapret_detail); l.addStretch(1)
        self.zapret_diag=QPushButton("Диагностика Zapret"); self.zapret_diag.setObjectName("secondary"); set_icon(self.zapret_diag, "activity", 18); self.zapret_diag.clicked.connect(self.show_diagnostics_tab); l.addWidget(self.zapret_diag); return c

    def _telegram_card(self):
        c,l=card(); l.setContentsMargins(18,16,18,16); row=QHBoxLayout(); t=QLabel("Telegram Proxy"); t.setObjectName("section"); self.tg_status=QLabel("○ Выключен"); row.addWidget(t); row.addStretch(1); row.addWidget(self.tg_status); l.addLayout(row); l.addWidget(self._separator())
        a=QLabel("Адрес"); a.setObjectName("muted"); l.addWidget(a); self.tg_address=QLabel(f"127.0.0.1:{self.config.get('telegram','port',default=1443)}"); self.tg_address.setObjectName("value"); l.addWidget(self.tg_address)
        b=QLabel("Активных соединений"); b.setObjectName("muted"); l.addWidget(b); self.tg_connections=QLabel("0"); self.tg_connections.setObjectName("value"); l.addWidget(self.tg_connections)
        self.tg_detail=QLabel(""); self.tg_detail.setObjectName("muted"); self.tg_detail.setWordWrap(True); l.addWidget(self.tg_detail); l.addStretch(1)
        self.tg_connect=QPushButton("Подключить Telegram"); self.tg_connect.setObjectName("secondary"); set_icon(self.tg_connect, "send", 18); self.tg_connect.clicked.connect(self.open_telegram_proxy); l.addWidget(self.tg_connect); return c

    def _stats_card(self):
        c, l = card(); c.setMinimumWidth(238); c.setMaximumWidth(278); l.setContentsMargins(18, 16, 18, 16); l.setSpacing(10)
        head = QHBoxLayout(); t = QLabel("Статистика"); t.setObjectName("section"); head.addWidget(t); head.addStretch(1)
        session = QLabel("Сессия"); session.setObjectName("pill"); head.addWidget(session); l.addLayout(head)
        row = QHBoxLayout(); row.setSpacing(10); self.donut = TrafficDonut(); row.addWidget(self.donut)
        nums = QVBoxLayout(); nums.setSpacing(2); nums.addStretch(1)
        self.stat_up = QLabel("↑ 0 КБ"); self.stat_up.setObjectName("ok"); nums.addWidget(self.stat_up)
        tx = QLabel("Отправлено"); tx.setObjectName("mutedSmall"); nums.addWidget(tx); nums.addSpacing(7)
        self.stat_down = QLabel("↓ 0 КБ"); self.stat_down.setObjectName("accent"); nums.addWidget(self.stat_down)
        rx = QLabel("Получено"); rx.setObjectName("mutedSmall"); nums.addWidget(rx); nums.addStretch(1); row.addLayout(nums); l.addLayout(row)
        l.addWidget(self._separator())
        self.stat_conn_value = QLabel("0"); self.stat_err_value = QLabel("0"); self.stat_bad_value = QLabel("0")
        for label, value in (("Активные соединения", self.stat_conn_value), ("Ошибок (WS)", self.stat_err_value), ("Плохих соединений", self.stat_bad_value)):
            r = QHBoxLayout(); a = QLabel(label); a.setObjectName("compact"); value.setObjectName("valueSmall"); r.addWidget(a); r.addStretch(1); r.addWidget(value); l.addLayout(r)
        return c

    def _home_updates_card(self):
        c,l=card(); c.setMinimumWidth(238); c.setMaximumWidth(278); l.setContentsMargins(18,16,18,16); row=QHBoxLayout(); t=QLabel("Обновления"); t.setObjectName("section"); row.addWidget(t); row.addStretch(1); self.home_refresh=QPushButton(); self.home_refresh.setObjectName("refreshButton"); self.home_refresh.setToolTip("Проверить наличие обновлений"); set_icon(self.home_refresh, "download", 18); self.home_refresh.clicked.connect(self.check_updates_now); row.addWidget(self.home_refresh); l.addLayout(row)
        z=QLabel("Zapret (Flowseal)"); z.setObjectName("value"); l.addWidget(z); self.home_update_z=QLabel("Проверка…"); self.home_update_z.setObjectName("muted"); l.addWidget(self.home_update_z); l.addSpacing(8)
        a=QLabel("Zapret+"); a.setObjectName("value"); l.addWidget(a); self.home_update_app=QLabel("Актуально"); self.home_update_app.setObjectName("muted"); l.addWidget(self.home_update_app); return c

    def _strategy_page(self):
        scroll,w,l=self._page_shell("Стратегии", "Автоматический режим сам подбирает рабочий профиль и запоминает его")
        c,cl=card()
        self.strategy_mode = QComboBox()
        self.strategy_mode.addItem("Автоматически (рекомендуется)", "auto")
        self.strategy_mode.addItem("Вручную", "manual")
        self.strategy_mode.setCurrentIndex(max(0, self.strategy_mode.findData(self.config.get("zapret","strategy_mode",default="auto"))))
        self.strategy_combo=QComboBox(); self.strategy_combo.addItems(self.controller.zapret.strategies()); cur=self.config.get('zapret','strategy',default='general.bat'); self.strategy_combo.setCurrentIndex(max(0,self.strategy_combo.findText(cur)))
        self.strategy_mode.currentIndexChanged.connect(lambda: self.strategy_combo.setEnabled(self.strategy_mode.currentData()=="manual"))
        self.strategy_combo.setEnabled(self.strategy_mode.currentData()=="manual")
        self.game_combo=QComboBox(); games=[("Выключен","off"),("TCP + UDP","all"),("Только TCP","tcp"),("Только UDP","udp")]
        for a,b in games:self.game_combo.addItem(a,b)
        self.game_combo.setCurrentIndex(max(0,self.game_combo.findData(self.config.get('zapret','game_filter',default='off'))))
        _m=QLabel("Режим"); _m.setObjectName("fieldLabel"); cl.addWidget(_m); cl.addWidget(self.strategy_mode)
        _a=QLabel("Текущая стратегия"); _a.setObjectName("fieldLabel"); cl.addWidget(_a); cl.addWidget(self.strategy_combo)
        self.strategy_auto_note=QLabel("Последняя рабочая стратегия: " + self.config.get("zapret","last_good_strategy",default="—")); self.strategy_auto_note.setObjectName("muted"); cl.addWidget(self.strategy_auto_note)
        self.auto_tune_btn=QPushButton("Подобрать стратегию сейчас"); set_icon(self.auto_tune_btn,"sliders-horizontal",18); self.auto_tune_btn.clicked.connect(self._run_strategy_tune); cl.addWidget(self.auto_tune_btn)
        cl.addSpacing(8); _b=QLabel("Game Filter"); _b.setObjectName("fieldLabel"); cl.addWidget(_b); cl.addWidget(self.game_combo)
        save=QPushButton("Сохранить и применить"); save.clicked.connect(self._apply_strategy_page); cl.addWidget(save); l.addWidget(c); l.addStretch(1); return scroll

    def _sites_page(self):
        scroll,w,l=self._page_shell("Сайты и списки", "Добавляйте свои домены поверх встроенных списков Flowseal")
        tabs=QTabWidget(); tabs.addTab(self._sites_tab(),"Мои сайты"); tabs.addTab(self._builtin_tab(),"Встроенные списки"); l.addWidget(tabs,1); return scroll

    def _sites_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,14,0,0); l.setSpacing(10); row=QHBoxLayout(); self.site_input=QLineEdit(); self.site_input.setPlaceholderText("Домен или ссылка, например example.com"); add=QPushButton("+ Добавить"); add.clicked.connect(self.add_site); self.site_input.returnPressed.connect(self.add_site); row.addWidget(self.site_input,1); row.addWidget(add); l.addLayout(row); self.sites=QListWidget(); l.addWidget(self.sites,1); rm=QPushButton("Удалить выбранный"); rm.setObjectName("secondary"); rm.clicked.connect(self.remove_site); l.addWidget(rm); return w

    def _builtin_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,14,0,0); self.builtin=QListWidget(); l.addWidget(self.builtin); return w

    def _telegram_page(self):
        scroll,w,l=self._page_shell("Telegram Proxy", "Локальный MTProto Proxy для Telegram Desktop")
        c,cl=card(); self.telegram_page_state=QLabel("Прокси выключен"); self.telegram_page_state.setObjectName("headlineSmall"); cl.addWidget(self.telegram_page_state); self.telegram_page_detail=QLabel(""); self.telegram_page_detail.setObjectName("muted"); cl.addWidget(self.telegram_page_detail); btn=QPushButton("Открыть настройку прокси в Telegram"); btn.clicked.connect(self.open_telegram_proxy); cl.addWidget(btn); l.addWidget(c); l.addStretch(1); return scroll

    def _updates_page(self):
        scroll,w,l=self._page_shell("Обновления", "Безопасная установка с проверкой и откатом")
        body=self._updates_tab(); l.addWidget(body,1); return scroll

    def _updates_tab(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setSpacing(12)
        ac,al=card(); _t=QLabel("Zapret+"); _t.setObjectName("section"); al.addWidget(_t); self.update_app_status=QLabel(""); self.update_app_status.setObjectName("muted"); self.update_app_status.setWordWrap(True); al.addWidget(self.update_app_status); self.update_app_btn=QPushButton("Обновить Zapret+ и перезапустить"); self.update_app_btn.clicked.connect(self.install_app_update); self.update_app_btn.setEnabled(False); al.addWidget(self.update_app_btn)
        zc,zl=card(); _t=QLabel("Движок Zapret"); _t.setObjectName("section"); zl.addWidget(_t); self.update_zapret_status=QLabel(""); self.update_zapret_status.setObjectName("muted"); self.update_zapret_status.setWordWrap(True); zl.addWidget(self.update_zapret_status); self.update_zapret_btn=QPushButton("Обновить Zapret сейчас"); self.update_zapret_btn.clicked.connect(self.install_zapret_update); self.update_zapret_btn.setEnabled(False); zl.addWidget(self.update_zapret_btn)
        tc,tl=card(); _t=QLabel("Telegram Proxy"); _t.setObjectName("section"); tl.addWidget(_t); self.update_tg_status=QLabel("Проверяем версию TG WS Proxy…"); self.update_tg_status.setObjectName("muted"); self.update_tg_status.setWordWrap(True); tl.addWidget(self.update_tg_status); self.update_tg_btn=QPushButton("Обновить TG WS Proxy"); self.update_tg_btn.clicked.connect(self.install_telegram_update); self.update_tg_btn.setEnabled(False); tl.addWidget(self.update_tg_btn)
        l.addWidget(ac); l.addWidget(zc); l.addWidget(tc); self.check_updates_btn=QPushButton("Проверить обновления"); self.check_updates_btn.clicked.connect(self.check_updates_now); l.addWidget(self.check_updates_btn); l.addStretch(1); return w

    def _diagnostics_page(self):
        scroll,w,l=self._page_shell("Диагностика", "Реальный статус движков и вывод winws")
        self.diag_text=QTextEdit(); self.diag_text.setReadOnly(True); self.diag_text.setObjectName("diagnostics"); l.addWidget(self.diag_text,1); row=QHBoxLayout(); a=QPushButton("Обновить"); a.clicked.connect(self.refresh_diagnostics); b=QPushButton("Скопировать отчёт"); b.clicked.connect(self.copy_diagnostics); row.addWidget(a); row.addWidget(b); row.addStretch(1); l.addLayout(row); return scroll

    def _separator(self):
        x=QFrame(); x.setObjectName("separator"); x.setFixedHeight(1); return x

    def _navigate(self,key):
        if key not in self.pages:return
        self.stack.setCurrentIndex(self.pages[key])
        for k, b in self.nav_buttons.items():
            active = k == key
            b.setChecked(active)
            set_icon(b, self.nav_icons[k], 20, active)
        if key=="diagnostics": self.refresh_diagnostics()
        if key=="updates": self.refresh_update_labels()

    def _game_label(self):
        return {"off":"Выключен","all":"TCP + UDP","tcp":"Только TCP","udp":"Только UDP"}.get(self.config.get('zapret','game_filter',default='off'),"Выключен")

    def _apply_strategy_page(self):
        self.config.data['zapret']['strategy_mode']=self.strategy_mode.currentData(); self.config.data['zapret']['strategy']=self.strategy_combo.currentText(); self.config.data['zapret']['game_filter']=self.game_combo.currentData(); self.config.save(); self.zapret_strategy.setText(self.strategy_combo.currentText()); self.zapret_game.setText(self._game_label())
        if self.controller.zapret.is_running():
            try:self.controller.zapret.restart(); self.info.setText("Стратегия применена.")
            except Exception as e: QMessageBox.warning(self,"Стратегия",str(e))
        self.refresh_status()

    def _apply_theme(self, theme):
        global _UI_ACCENT, _UI_NEUTRAL
        self._theme = "light" if theme == "light" else "dark"
        self._accent = self.config.get("app", "accent_color", default=DEFAULT_ACCENT) or DEFAULT_ACCENT
        if not QColor(self._accent).isValid():
            self._accent = DEFAULT_ACCENT
        _UI_ACCENT = QColor(self._accent).name().upper()
        _UI_NEUTRAL = "#65758B" if self._theme == "light" else "#9AA7B9"
        self.config.data.setdefault("app", {})["theme"] = self._theme
        self.config.data.setdefault("app", {})["accent_color"] = _UI_ACCENT
        self.config.save()
        QApplication.instance().setStyleSheet(build_style(self._theme, _UI_ACCENT))
        if hasattr(self, "donut"):
            self.donut.setProperty("theme", self._theme); self.donut.update()
        # Repaint icons so active navigation follows the chosen accent.
        if hasattr(self, "nav_buttons"):
            for k, b in self.nav_buttons.items():
                set_icon(b, self.nav_icons[k], 19, b.isChecked())
        if hasattr(self, "settings_button"): set_icon(self.settings_button, "settings", 19)
        if hasattr(self, "theme_button"): set_icon(self.theme_button, "moon" if self._theme == "light" else "sun", 19)
        if hasattr(self, "toggle"): set_icon(self.toggle, "power", 20, color="#FFFFFF" if self._theme=="light" else "#101419")
        if hasattr(self, "zapret_diag"): set_icon(self.zapret_diag, "activity", 18)
        if hasattr(self, "tg_connect"): set_icon(self.tg_connect, "send", 18)
        if hasattr(self, "home_refresh"): set_icon(self.home_refresh, "download", 18)

    def toggle_theme(self):
        self.config.data.setdefault("app", {})["theme_user_selected"] = True
        self.config.save()
        self._apply_theme("light" if self._theme == "dark" else "dark")

    def refresh_update_labels(self):
        if not hasattr(self, "update_zapret_status"):
            return
        local_z = "?"
        try:
            from core.paths import ZAPRET_DIR
            vp = ZAPRET_DIR / "version.txt"
            local_z = vp.read_text(encoding="utf-8", errors="replace").strip() if vp.exists() else "?"
        except Exception:
            pass
        self.update_zapret_status.setText(f"Установлено: {local_z} · проверка ещё не выполнена")
        self.home_update_z.setText("Проверяем актуальность…")
        self.update_app_status.setText(f"Установлено: {__version__} · автообновление приложения активируется после публикации официального репозитория Zapret+.")
        self.home_update_app.setText("Актуально")
        try:
            from services.updater import _read_local_telegram_version
            local_tg = _read_local_telegram_version()
        except Exception:
            local_tg = "?"
        self.update_tg_status.setText(f"Установлено: {local_tg} · проверка ещё не выполнена")

    def _set_update_busy(self, busy: bool):
        if hasattr(self, "check_updates_btn"):
            self.check_updates_btn.setEnabled(not busy)
            self.check_updates_btn.setText("Проверяем…" if busy else "Проверить обновления")
        if hasattr(self, "home_refresh"):
            self.home_refresh.setEnabled(not busy)
            self.home_refresh.setToolTip("Проверяем обновления…" if busy else "Проверить обновления")
        if hasattr(self, "update_zapret_btn"):
            available = bool((self._last_update_info or {}).get("zapret", {}).get("available"))
            self.update_zapret_btn.setEnabled((not busy) and available)
        if hasattr(self, "update_app_btn"):
            available = bool((self._last_update_info or {}).get("app", {}).get("available"))
            self.update_app_btn.setEnabled((not busy) and available)
        if hasattr(self, "update_tg_btn"):
            available = bool((self._last_update_info or {}).get("telegram", {}).get("available"))
            self.update_tg_btn.setEnabled((not busy) and available)

    def _run_update_worker(self, mode: str, release=None, callback=None):
        if self._update_worker and self._update_worker.isRunning():
            return
        self._set_update_busy(True)
        worker = UpdateWorker(self.controller.updater, mode, release, self)
        self._update_worker = worker
        def ok(result):
            self._set_update_busy(False)
            if callback:
                callback(result)
        def bad(message):
            self._set_update_busy(False)
            self.info.setText("Обновление не выполнено: " + message)
            if mode != "auto":
                QMessageBox.warning(self, "Zapret+ — обновление", message)
        worker.done.connect(ok); worker.failed.connect(bad)
        worker.finished.connect(lambda: setattr(self, "_update_worker", None))
        worker.start()

    def check_updates_now(self):
        self._run_update_worker("check", callback=self._update_check_finished)

    def _update_check_finished(self, info):
        self._last_update_info = info
        z = info.get("zapret", {})
        if z.get("remote"):
            text = f"Установлено: {z.get('local')} · GitHub: {z.get('remote')}"
            if z.get("available"):
                text += " · доступно обновление"
            else:
                text += " · актуально"
            self.update_zapret_status.setText(text)
            self.update_zapret_btn.setEnabled(bool(z.get("available")))
            self.home_update_z.setText("Доступно обновление" if z.get("available") else "Актуально")
        else:
            self.update_zapret_status.setText(f"Установлено: {z.get('local','?')} · GitHub сейчас недоступен")
            self.home_update_z.setText("Не удалось проверить")

        app = info.get("app", {})
        if not app.get("configured"):
            self.update_app_status.setText(f"Установлено: {__version__} · механизм self-update готов, но официальный GitHub-репозиторий Zapret+ ещё не задан в сборке.")
        elif app.get("remote"):
            suffix = " · доступно обновление" if app.get("available") else " · актуально"
            self.update_app_status.setText(f"Установлено: {app.get('local')} · GitHub: {app.get('remote')}{suffix}")
            self.update_app_btn.setEnabled(bool(app.get("available")))
            self.home_update_app.setText("Доступно обновление" if app.get("available") else "Актуально")

        tg = info.get("telegram", {})
        if tg.get("remote"):
            suffix = " · доступно обновление" if tg.get("available") else " · актуально"
            self.update_tg_status.setText(
                f"Установлено: {tg.get('local','?')} · Flowseal/tg-ws-proxy: {tg.get('remote')}{suffix}"
            )
            self.update_tg_btn.setEnabled(bool(tg.get("available")))
        else:
            self.update_tg_status.setText(
                f"Установлено: {tg.get('local','?')} · GitHub сейчас недоступен"
            )
            self.update_tg_btn.setEnabled(False)

    def run_startup_updates(self):
        self._run_update_worker("check", callback=self._startup_check_finished)

    def _startup_check_finished(self, info):
        self._update_check_finished(info)
        if not self.config.get("app", "auto_update", default=True):
            return
        app = info.get("app", {})
        if app.get("available") and app.get("configured"):
            self.info.setText("Найдена новая версия Zapret+. Скачиваем и проверяем пакет…")
            QTimer.singleShot(200, lambda: self._run_update_worker("app", app.get("release"), callback=self._app_stage_finished))
            return
        if self.config.get("app", "auto_update_zapret", default=True) and info.get("zapret", {}).get("available"):
            QTimer.singleShot(200, lambda: self._run_update_worker("auto", callback=self._startup_update_finished))

    def _startup_update_finished(self, results):
        changed = [r for r in results if getattr(r, "changed", False)]
        if changed:
            self.info.setText(" · ".join(r.message for r in changed))
            self.refresh_builtin(); self.refresh_status(); self.refresh_diagnostics()
        QTimer.singleShot(150, self.check_updates_now)

    def install_app_update(self):
        release = None
        if self._last_update_info:
            release = self._last_update_info.get("app", {}).get("release")
        self.info.setText("Скачиваем и проверяем обновление Zapret+…")
        self._run_update_worker("app", release, callback=self._app_stage_finished)

    def _app_stage_finished(self, plan):
        try:
            self.info.setText(f"Zapret+ {plan.new_version} проверен. Перезапускаем приложение…")
            self.controller.updater.launch_app_update(plan)
            if self.tray is not None:
                self.tray.hide()
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
        except Exception as exc:
            QMessageBox.warning(self, "Zapret+ — обновление", f"Не удалось запустить установщик:\n{exc}")

    def install_telegram_update(self):
        """TG WS Proxy is embedded; update it through a compatible Zapret+ release."""
        tg = (self._last_update_info or {}).get("telegram", {})
        app = (self._last_update_info or {}).get("app", {})
        if not tg.get("available"):
            self.info.setText("TG WS Proxy уже актуален.")
            return

        if app.get("available") and app.get("configured"):
            answer = QMessageBox.question(
                self,
                "Обновление TG WS Proxy",
                "Новая версия TG WS Proxy входит в совместимое обновление Zapret+.\n\n"
                "Обновить Zapret+ сейчас?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.install_app_update()
            return

        QMessageBox.information(
            self,
            "Обновление TG WS Proxy",
            f"У Flowseal доступен TG WS Proxy {tg.get('remote','')}, "
            f"а в этой сборке Zapret+ используется {tg.get('local','?')}.\n\n"
            "TG WS Proxy встроен в Zapret+ и связан с нашей интеграцией, поэтому "
            "приложение не заменяет его Python-файлы напрямую. Это защищает от "
            "поломки Telegram после несовместимого upstream-обновления.\n\n"
            "Обновление установится вместе с совместимой версией Zapret+."
        )

    def install_zapret_update(self):
        release = None
        if self._last_update_info:
            release = self._last_update_info.get("zapret", {}).get("release")
        self.info.setText("Скачиваем и проверяем обновление Zapret…")
        self._run_update_worker("zapret", release, callback=self._zapret_update_finished)

    def _zapret_update_finished(self, result):
        self.info.setText(result.message)
        self.refresh_builtin(); self.refresh_status(); self.refresh_diagnostics(); QTimer.singleShot(150, self.check_updates_now)

    def _set_tray_toggle_text(self, text: str):
        if self.tray_toggle is not None:
            self.tray_toggle.setText(text)

    def init_optional_services(self):
        """Initialize non-critical desktop integrations after the window exists."""
        try:
            self._build_tray()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("System tray initialization failed")
            self.tray = None
            self.tray_toggle = None
        if self.config.get("app", "check_updates", default=True):
            QTimer.singleShot(1800, self.run_startup_updates)

    def _build_tray(self):
        if self.tray is not None:
            return
        self.tray = QSystemTrayIcon(self)
        app_icon = ASSETS_DIR / "icon.ico"
        if app_icon.exists():
            self.tray.setIcon(QIcon(str(app_icon)))
        menu = QMenu()
        self.tray_toggle = QAction("Включить всё", self); self.tray_toggle.triggered.connect(self.toggle_all)
        tg = QAction("Подключить Telegram", self); tg.triggered.connect(self.open_telegram_proxy)
        show = QAction("Открыть Zapret+", self); show.triggered.connect(self.restore_window)
        quit_action = QAction("Выход", self); quit_action.triggered.connect(self.quit_app)
        menu.addAction(self.tray_toggle); menu.addAction(tg); menu.addSeparator(); menu.addAction(show); menu.addSeparator(); menu.addAction(quit_action)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason: self.restore_window() if reason == QSystemTrayIcon.Trigger else None); self.tray.show()

    def _start_failure_message(self, result: dict) -> str:
        blocks = []
        z = result.get("zapret", {})
        t = result.get("telegram", {})
        if z.get("requested") and z.get("ok") is False:
            blocks.append("Zapret не запустился:\n" + (z.get("error") or "неизвестная ошибка"))
        if t.get("requested") and t.get("ok") is False:
            blocks.append("Telegram Proxy не запустился:\n" + (t.get("error") or "неизвестная ошибка"))
        return "\n\n".join(blocks)

    def _set_engine_busy(self, mode: str | None):
        """Keep the primary action visibly alive while engines transition."""
        self._engine_operation = mode
        if mode:
            self._busy_angle = 0
            self.toggle.setEnabled(False)
            self.toggle.setCursor(Qt.BusyCursor)
            self.toggle.setProperty("busy", True)
            self._busy_timer.start()
            self._busy_tick()
        else:
            self._busy_timer.stop()
            self.toggle.setEnabled(True)
            self.toggle.setCursor(Qt.PointingHandCursor)
            self.toggle.setProperty("busy", False)
            set_icon(self.toggle, "power", 20, color="#FFFFFF" if self._theme=="light" else "#101419")
        self.toggle.style().unpolish(self.toggle)
        self.toggle.style().polish(self.toggle)

    def _busy_tick(self):
        if not self._engine_operation:
            return
        # Small animated arc rendered directly into the button icon. No GIF/assets,
        # so it follows DPI scaling and remains crisp on every Windows scale factor.
        size = 20
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#FFFFFF" if self._theme=="light" else "#101419"), 2.2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(3.2, 3.2, size - 6.4, size - 6.4)
        p.drawArc(rect, self._busy_angle * 16, -250 * 16)
        p.end()
        self.toggle.setIcon(QIcon(pm))
        self.toggle.setIconSize(QSize(size, size))
        self._busy_angle = (self._busy_angle - 24) % 360
        phase = (abs(self._busy_angle) // 24) % 4
        dots = "." * phase
        base = "Включаем" if self._engine_operation == "start" else "Выключаем"
        self.toggle.setText(base + dots)

    def toggle_all(self):
        """Start/stop asynchronously so the window never feels frozen."""
        if self._engine_worker and self._engine_worker.isRunning():
            return
        mode = "stop" if self.controller.active else "start"
        self._set_engine_busy(mode)
        self.info.setText("Останавливаем компоненты…" if mode == "stop" else "Запускаем защиту…")

        worker = EngineWorker(self.controller, mode, self)
        self._engine_worker = worker

        def finished(result):
            self._set_engine_busy(None)
            if mode == "stop":
                self.info.setText("Компоненты остановлены.")
            else:
                failures = self._start_failure_message(result)
                z_notice = result.get("zapret", {}).get("notice", "")
                if z_notice:
                    self.info.setText("Защита включена. Старый конфликтующий winws остановлен автоматически.")
                elif self.controller.telegram_setup_requested:
                    self.info.setText("Защита включена. Telegram получил настройки локального прокси.")
                else:
                    self.info.setText("Защита включена.")
                if failures:
                    self.refresh_diagnostics()
                    QMessageBox.warning(
                        self,
                        "Zapret+ — частичный запуск",
                        failures + "\n\nКомпоненты работают независимо. Подробности доступны в «Диагностике».",
                    )
            self.refresh_status()

        def failed(message):
            self._set_engine_busy(None)
            self.info.setText("Не удалось изменить состояние.")
            self.refresh_status()
            QMessageBox.critical(self, "Zapret+", f"Не удалось изменить состояние:\n{message}")

        worker.done.connect(finished)
        worker.failed.connect(failed)
        worker.finished.connect(lambda: setattr(self, "_engine_worker", None))
        worker.start()

    def _run_strategy_tune(self):
        if self._strategy_worker and self._strategy_worker.isRunning():
            return
        if not self.controller.zapret.is_running():
            QMessageBox.information(self, "Автоподбор", "Сначала включите защиту. Автоподбор проверяет реальные HTTPS-соединения через работающий Zapret.")
            return
        self.auto_tune_btn.setEnabled(False)
        self.auto_tune_btn.setText("Подбираем…")
        self.info.setText("Автоподбор стратегии запущен. Проверяем реальные HTTPS-подключения…")
        worker = StrategyTuneWorker(self.controller, self)
        self._strategy_worker = worker

        def done(result):
            self.auto_tune_btn.setEnabled(True)
            self.auto_tune_btn.setText("Подобрать стратегию сейчас")
            self.strategy_combo.setCurrentIndex(max(0,self.strategy_combo.findText(result.strategy)))
            self.strategy_auto_note.setText(f"Последняя рабочая стратегия: {result.strategy} · {result.median_ms} мс")
            self.zapret_strategy.setText(result.strategy)
            self.info.setText(f"Автоподбор завершён: {result.strategy}")
            self.refresh_status()

        def failed(message):
            self.auto_tune_btn.setEnabled(True)
            self.auto_tune_btn.setText("Подобрать стратегию сейчас")
            self.info.setText("Автоподбор не смог подтвердить рабочую стратегию.")
            QMessageBox.warning(self, "Автоподбор стратегии", message)

        worker.done.connect(done)
        worker.failed.connect(failed)
        worker.finished.connect(lambda: setattr(self, "_strategy_worker", None))
        worker.start()

    def _watchdog_tick(self):
        if not self.config.get("app", "auto_recover", default=True):
            return
        if self._engine_operation or (self._engine_worker and self._engine_worker.isRunning()):
            return
        if self._strategy_worker and self._strategy_worker.isRunning():
            return
        if self._watchdog_worker and self._watchdog_worker.isRunning():
            return
        worker = WatchdogWorker(self.controller, self)
        self._watchdog_worker = worker

        def done(notes):
            if notes:
                self.info.setText(" · ".join(notes))
                self.refresh_status()

        worker.done.connect(done)
        worker.failed.connect(lambda message: None)  # watchdog failures stay non-modal
        worker.finished.connect(lambda: setattr(self, "_watchdog_worker", None))
        worker.start()

    def refresh_status(self):
        st = self.controller.status(); z = st["zapret"]; zerr = st.get("zapret_error", ""); th = st["telegram"]
        if self.controller.operation_note and not self._engine_operation:
            self.info.setText(self.controller.operation_note)
        t_running = th["running"] if th is not None else None
        expected = [v for v in (z, t_running) if v is not None]
        all_on = bool(expected) and all(expected); any_on = any(v is True for v in expected)
        self.controller.active = any_on

        # Start the clock only when protection becomes active; reset it when everything stops.
        if any_on and not self._last_any_on:
            self._protection_started_at = time.monotonic()
            self._protection_started_wall = datetime.now()
        elif not any_on and self._last_any_on:
            self._protection_started_at = None
            self._protection_started_wall = None
        self._last_any_on = any_on

        self.zapret_strategy.setText(self.config.get('zapret','strategy',default='general.bat'))
        self.zapret_game.setText(self._game_label())
        if z is None:
            self.zapret_status.setText("— Отключён"); self.zapret_status.setObjectName("muted"); self.zapret_detail.setText("")
        elif z:
            self.zapret_status.setText("● Работает"); self.zapret_status.setObjectName("ok")
            self.zapret_detail.setText("Конфликтов не обнаружено" if not self.controller.zapret.last_resolution else "Старый конфликтующий winws устранён автоматически")
        elif zerr:
            self.zapret_status.setText("● Ошибка"); self.zapret_status.setObjectName("error"); self.zapret_detail.setText(zerr.splitlines()[0])
        else:
            self.zapret_status.setText("○ Выключен"); self.zapret_status.setObjectName("muted"); self.zapret_detail.setText("")

        if th is None:
            self.tg_status.setText("— Отключён"); self.tg_status.setObjectName("muted"); self.tg_detail.setText(""); self.tg_connect.setVisible(False)
            self.tg_connections.setText("0"); self.telegram_page_state.setText("Telegram Proxy отключён в настройках"); self.telegram_page_detail.setText("")
            up=down=conn=err=bad=0
        elif not th["running"]:
            self.tg_status.setText("● Ошибка" if th["error"] else "○ Выключен"); self.tg_status.setObjectName("error" if th["error"] else "muted")
            self.tg_detail.setText(th["error"] or ""); self.tg_connect.setVisible(True); self.tg_connections.setText("0")
            self.telegram_page_state.setText("Прокси выключен"); self.telegram_page_detail.setText(th["error"] or f"127.0.0.1:{self.config.get('telegram','port',default=1443)}")
            up=down=conn=err=bad=0
        else:
            conn=int(th.get("active_connections",0)); up=int(th.get("bytes_up",0)); down=int(th.get("bytes_down",0)); err=int(th.get("ws_errors",0)); bad=int(th.get("bad_connections",0))
            self.tg_connections.setText(str(conn)); self.tg_address.setText(f"127.0.0.1:{self.config.get('telegram','port',default=1443)}")
            if th["client_seen"]:
                self.tg_status.setText("● Подключён"); self.tg_status.setObjectName("ok"); self.tg_detail.setText(f"Передано: {up/1024:.1f} КБ / {down/1024:.1f} КБ"); self.tg_connect.setVisible(False)
                self.telegram_page_state.setText("Telegram подключён"); self.telegram_page_detail.setText(f"{conn} активных соединений · ↑ {up/1024:.1f} КБ · ↓ {down/1024:.1f} КБ")
            else:
                self.tg_status.setText("● Прокси готов"); self.tg_status.setObjectName("warn"); self.tg_detail.setText("Ожидаем подключение Telegram"); self.tg_connect.setVisible(True)
                self.telegram_page_state.setText("Прокси готов"); self.telegram_page_detail.setText("Telegram ещё не подключался к локальному прокси")

        for widget in (self.zapret_status, self.tg_status):
            widget.style().unpolish(widget); widget.style().polish(widget)

        self.donut.set_values(up, down); self.stat_up.setText(f"↑ {up/1024:.1f} КБ"); self.stat_down.setText(f"↓ {down/1024:.1f} КБ")
        self.stat_conn_value.setText(str(conn))
        self.stat_err_value.setText(str(err))
        self.stat_bad_value.setText(str(bad))

        if self._protection_started_at is not None and self._protection_started_wall is not None:
            elapsed = max(0, int(time.monotonic() - self._protection_started_at))
            hh, rem = divmod(elapsed, 3600); mm, ss = divmod(rem, 60)
            self.footer_time.setText(
                f"Запущено: {self._protection_started_wall:%d.%m.%Y %H:%M:%S}     "
                f"Время работы: {hh:02d}:{mm:02d}:{ss:02d}"
            )
        else:
            self.footer_time.setText("Запущено: —     Время работы: 00:00:00")
        if self._engine_operation == "start":
            self.overall.setText("Включаем защиту")
            self.overall_hint.setText("Запускаем Zapret и Telegram Proxy…")
            self._set_tray_toggle_text("Включение…")
            self.footer_state.setText("◌  Идёт запуск компонентов")
            self.footer_state.setObjectName("accent")
        elif self._engine_operation == "stop":
            self.overall.setText("Выключаем защиту")
            self.overall_hint.setText("Корректно останавливаем компоненты…")
            self._set_tray_toggle_text("Выключение…")
            self.footer_state.setText("◌  Идёт остановка компонентов")
            self.footer_state.setObjectName("accent")
        elif all_on:
            self.overall.setText("Защита включена"); self.overall_hint.setText("Zapret и Telegram Proxy работают в штатном режиме"); self.toggle.setText("Выключить всё"); self._set_tray_toggle_text("Выключить всё"); self.footer_state.setText("●  Система защищена и работает стабильно"); self.footer_state.setObjectName("ok")
        elif any_on:
            self.overall.setText("Частичная защита"); self.overall_hint.setText("Один из компонентов работает"); self.toggle.setText("Выключить всё"); self._set_tray_toggle_text("Выключить всё"); self.footer_state.setText("●  Частичная защита"); self.footer_state.setObjectName("warn")
        else:
            self.overall.setText("Защита выключена"); self.overall_hint.setText("Zapret и Telegram Proxy остановлены"); self.toggle.setText("Включить всё"); self._set_tray_toggle_text("Включить всё"); self.footer_state.setText("○  Защита выключена"); self.footer_state.setObjectName("muted")
        self.footer_state.style().unpolish(self.footer_state); self.footer_state.style().polish(self.footer_state)

    def refresh_sites(self):
        self.sites.clear()
        for item in self.config.data.get("sites", []):
            if isinstance(item, dict) and item.get("domain"):
                self.sites.addItem(QListWidgetItem(item["domain"]))

    def refresh_builtin(self):
        self.builtin.clear(); groups = self.controller.zapret.built_in_sites()
        labels = {"list-google.txt": "YouTube / Google", "list-general.txt": "Discord / общие", "list-exclude.txt": "Исключения"}
        for filename, domains in groups.items():
            head = QListWidgetItem(f"── {labels.get(filename, filename)} · {len(domains)} ──"); head.setFlags(Qt.NoItemFlags); self.builtin.addItem(head)
            for domain in domains:
                self.builtin.addItem(QListWidgetItem(domain))

    def refresh_diagnostics(self):
        if hasattr(self, "diag_text"):
            self.diag_text.setPlainText(self.controller.diagnostics())

    def copy_diagnostics(self):
        text = self.controller.diagnostics()
        QGuiApplication.clipboard().setText(text)
        self.info.setText("Диагностический отчёт скопирован в буфер обмена.")

    def show_diagnostics_tab(self):
        self.refresh_diagnostics(); self._navigate("diagnostics")

    def add_site(self):
        try:
            d = self.controller.add_site(self.site_input.text()); self.site_input.clear(); self.refresh_sites(); self.info.setText(f"Добавлен: {d}. Zapret автоматически применил пользовательский список.")
        except Exception as exc:
            QMessageBox.warning(self, "Сайт", str(exc))

    def remove_site(self):
        item = self.sites.currentItem()
        if not item: return
        self.controller.remove_site(item.text()); self.refresh_sites()

    def open_telegram_proxy(self):
        try:
            if not self.controller.telegram.is_running():
                # Start Telegram independently. Never route this action through Zapret.
                self.controller.telegram.start()
                self.controller.active = self.controller.zapret.is_running() or self.controller.telegram.is_running()
            if not self.controller.telegram.open_in_telegram(mark_configured=True):
                raise RuntimeError(self.controller.telegram.last_error or "Windows не смог открыть tg:// ссылку")
            self.info.setText("Telegram открыт с настройкой MTProto Proxy. Подтвердите подключение в Telegram.")
        except Exception as exc:
            QMessageBox.warning(self, "Telegram", f"Не удалось открыть настройку прокси:\n{exc}\n\nСсылка:\n{self.controller.telegram.proxy_url()}")
        self.refresh_status(); self.refresh_diagnostics()

    def open_settings(self):
        dlg = SettingsDialog(self.controller, self.config, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self._apply_theme(self.config.get("app", "theme", default="light"))
            self._apply_responsive_layout(self.width())
            self.refresh_status(); self.refresh_diagnostics()

    def restore_window(self):
        """Restore through Qt so layouts/widgets are repainted correctly."""
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.update()
        if self.centralWidget() is not None:
            self.centralWidget().update()

    def closeEvent(self, event: QCloseEvent):
        """A normal window close is a real application exit.

        Previous builds hid the Qt window in the tray. A later desktop-shortcut launch
        found that hidden native HWND and restored it outside Qt's normal show path,
        which could leave a blank/non-responsive surface. Closing now means closing.
        """
        try:
            if self.tray is not None:
                self.tray.hide()
        except Exception:
            pass
        event.accept()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        QTimer.singleShot(0, QApplication.quit)

    def quit_app(self):
        try: self.controller.stop_all()
        except Exception: pass
        if self.tray is not None: self.tray.hide()
        from PySide6.QtWidgets import QApplication; QApplication.quit()


class SettingsDialog(QDialog):
    GAME_ITEMS = [("Выключен", "off"), ("TCP + UDP", "all"), ("Только TCP", "tcp"), ("Только UDP", "udp")]

    def __init__(self, controller, config, parent=None):
        super().__init__(parent); self.controller = controller; self.config = config
        self.setWindowTitle("Настройки Zapret+"); self.resize(560, 600)
        form = QFormLayout(self); form.setSpacing(12)
        self.z_enabled = QCheckBox(); self.z_enabled.setChecked(config.get("zapret","enabled",default=True))
        self.t_enabled = QCheckBox(); self.t_enabled.setChecked(config.get("telegram","enabled",default=True))
        self.strategy_mode = QComboBox(); self.strategy_mode.addItem("Автоматически", "auto"); self.strategy_mode.addItem("Вручную", "manual")
        self.strategy_mode.setCurrentIndex(max(0,self.strategy_mode.findData(config.get("zapret","strategy_mode",default="auto"))))
        self.strategy = QComboBox(); self.strategy.addItems(controller.zapret.strategies())
        current = config.get("zapret","strategy",default="general.bat"); idx = self.strategy.findText(current); self.strategy.setCurrentIndex(max(0, idx))
        self.strategy.setEnabled(self.strategy_mode.currentData()=="manual")
        self.strategy_mode.currentIndexChanged.connect(lambda: self.strategy.setEnabled(self.strategy_mode.currentData()=="manual"))
        self.game = QComboBox()
        for label, value in self.GAME_ITEMS: self.game.addItem(label, value)
        gi = self.game.findData(config.get("zapret","game_filter",default="off")); self.game.setCurrentIndex(max(0, gi))
        self.port = QSpinBox(); self.port.setRange(1024,65535); self.port.setValue(int(config.get("telegram","port",default=1443)))
        self.cfproxy = QCheckBox(); self.cfproxy.setChecked(config.get("telegram","cfproxy",default=True))
        self.auto_tg = QCheckBox(); self.auto_tg.setChecked(config.get("telegram","auto_configure_client",default=True))
        self.autorun = QCheckBox(); self.autorun.setChecked(config.get("app","start_with_windows",default=False))
        self.auto_start = QCheckBox(); self.auto_start.setChecked(config.get("app","auto_start_protection",default=False))
        self.check_updates = QCheckBox(); self.check_updates.setChecked(config.get("app","check_updates",default=True))
        self.auto_update = QCheckBox(); self.auto_update.setChecked(config.get("app","auto_update",default=True))
        self.auto_update_zapret = QCheckBox(); self.auto_update_zapret.setChecked(config.get("app","auto_update_zapret",default=True))
        self.auto_recover = QCheckBox(); self.auto_recover.setChecked(config.get("app","auto_recover",default=True))

        self.theme = QComboBox(); self.theme.addItem("Тёмная", "dark"); self.theme.addItem("Светлая", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(config.get("app", "theme", default="light"))))
        self.accent_edit = QLineEdit(config.get("app", "accent_color", default=DEFAULT_ACCENT)); self.accent_edit.setMaximumWidth(110)
        self.accent_button = QPushButton(); self.accent_button.setObjectName("accentPreview"); self.accent_button.setToolTip("Выбрать акцентный цвет")
        self.accent_button.clicked.connect(self._choose_accent)
        self.accent_edit.textChanged.connect(self._refresh_accent_preview)
        accent_row = QWidget(); accent_layout = QHBoxLayout(accent_row); accent_layout.setContentsMargins(0,0,0,0); accent_layout.setSpacing(8); accent_layout.addWidget(self.accent_edit); accent_layout.addWidget(self.accent_button); accent_layout.addStretch(1)
        self.compact_sidebar = QCheckBox(); self.compact_sidebar.setChecked(config.get("app", "compact_sidebar", default=False))
        self._refresh_accent_preview()

        form.addRow("Тема интерфейса", self.theme); form.addRow("Акцентный цвет", accent_row); form.addRow("Компактная боковая панель", self.compact_sidebar)
        form.addRow("Использовать Zapret", self.z_enabled); form.addRow("Режим стратегии", self.strategy_mode); form.addRow("Стратегия", self.strategy); form.addRow("Game Filter", self.game)
        form.addRow("Использовать Telegram Proxy", self.t_enabled); form.addRow("Порт Telegram Proxy", self.port); form.addRow("Cloudflare fallback", self.cfproxy)
        form.addRow("Автоматически открыть настройку Telegram", self.auto_tg); form.addRow("Запускать с Windows", self.autorun); form.addRow("Сразу включать всё", self.auto_start)
        form.addRow("Автовосстановление компонентов", self.auto_recover); form.addRow("Проверять обновления", self.check_updates); form.addRow("Устанавливать обновления автоматически", self.auto_update); form.addRow("Автообновлять движок Zapret", self.auto_update_zapret)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); box.accepted.connect(self.accept); box.rejected.connect(self.reject); form.addRow(box)

    def _refresh_accent_preview(self):
        c = QColor(self.accent_edit.text().strip())
        color = c.name().upper() if c.isValid() else DEFAULT_ACCENT
        self.accent_button.setStyleSheet(f"background:{color}; border:1px solid {color}; border-radius:8px;")

    def _choose_accent(self):
        current = QColor(self.accent_edit.text().strip())
        if not current.isValid(): current = QColor(DEFAULT_ACCENT)
        chosen = QColorDialog.getColor(current, self, "Акцентный цвет Zapret+")
        if chosen.isValid(): self.accent_edit.setText(chosen.name().upper())

    def apply(self):
        d = self.config.data; old_tg_sig = self.controller.telegram.setup_signature()
        d["zapret"]["enabled"] = self.z_enabled.isChecked(); d["zapret"]["strategy_mode"] = self.strategy_mode.currentData(); d["zapret"]["strategy"] = self.strategy.currentText(); d["zapret"]["game_filter"] = self.game.currentData()
        d["telegram"]["enabled"] = self.t_enabled.isChecked(); d["telegram"]["port"] = self.port.value(); d["telegram"]["cfproxy"] = self.cfproxy.isChecked(); d["telegram"]["auto_configure_client"] = self.auto_tg.isChecked()
        d["app"]["start_with_windows"] = self.autorun.isChecked(); d["app"]["auto_start_protection"] = self.auto_start.isChecked()
        d["app"]["auto_recover"] = self.auto_recover.isChecked(); d["app"]["check_updates"] = self.check_updates.isChecked(); d["app"]["auto_update"] = self.auto_update.isChecked(); d["app"]["auto_update_zapret"] = self.auto_update_zapret.isChecked()
        d["app"]["theme"] = self.theme.currentData(); d["app"]["theme_user_selected"] = True
        c = QColor(self.accent_edit.text().strip()); d["app"]["accent_color"] = c.name().upper() if c.isValid() else DEFAULT_ACCENT
        d["app"]["compact_sidebar"] = self.compact_sidebar.isChecked()
        self.config.save()
        if self.controller.telegram.setup_signature() != old_tg_sig:
            d["telegram"]["setup_signature"] = ""; self.config.save()
        try:
            from services.autostart import set_autostart; set_autostart(self.autorun.isChecked())
        except Exception: pass
