import sys
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QListWidget, QLabel, QFileDialog,
    QTabWidget, QListWidgetItem, QProgressBar, QGroupBox, QMessageBox,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QFont, QPalette, QColor

# libtorrent'ı import et
try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError as e:
    LIBTORRENT_AVAILABLE = False
    print(f"libtorrent import hatası: {e}")
    print("\nLütfen şu komutları çalıştırın:")
    print("  pip install libtorrent")
    print("  pip install libtorrent-windows-dll")
    print("\nAyrıca Microsoft Visual C++ Redistributable yüklü olduğundan emin olun:")
    print("  https://aka.ms/vs/17/release/vc_redist.x64.exe")


class DownloadThread(QThread):
    """Torrent indirme thread'i - libtorrent kullanır"""
    progress = pyqtSignal(int, str, float, float)  # progress, status, download_speed, upload_speed
    finished = pyqtSignal(str, bool)  # download path, success
    paused = pyqtSignal()
    resumed = pyqtSignal()
    
    def __init__(self, magnet_url, download_path, download_id):
        super().__init__()
        self.magnet_url = magnet_url
        self.download_path = download_path
        self.download_id = download_id
        self.stop_requested = False
        self.pause_requested = False
        self.resume_requested = False
        self.is_paused = False
        self.ses = None
        self.handle = None
        
    def run(self):
        if not LIBTORRENT_AVAILABLE:
            self.progress.emit(0, "libtorrent kütüphanesi bulunamadı", 0, 0)
            self.finished.emit(self.download_path, False)
            return
            
        try:
            # libtorrent session oluştur
            self.ses = lt.session()
            
            # Ayarları tek tek dene (bazıları mevcut olmayabilir)
            settings_to_try = {
                'enable_dht': True,
                'enable_lsd': True,
                'enable_upnp': True,
                'enable_natpmp': True,
                'listen_interfaces': '0.0.0.0:6881',
            }
            
            # PEX ayarını dene (bazı sürümlerde farklı isimle olabilir)
            pex_variants = ['enable_pex', 'enable_peer_exchange', 'pex']
            for pex_name in pex_variants:
                try:
                    test_settings = {pex_name: True}
                    self.ses.apply_settings(test_settings)
                    settings_to_try[pex_name] = True
                    break
                except:
                    continue
            
            # Geçerli ayarları uygula
            valid_settings = {}
            for key, value in settings_to_try.items():
                try:
                    test_dict = {key: value}
                    self.ses.apply_settings(test_dict)
                    valid_settings[key] = value
                except (KeyError, AttributeError, TypeError):
                    continue
            
            # Tüm geçerli ayarları bir kerede uygula
            if valid_settings:
                try:
                    self.ses.apply_settings(valid_settings)
                except Exception:
                    pass
            
            # İndirme klasörünün var olduğundan emin ol
            Path(self.download_path).mkdir(parents=True, exist_ok=True)
            
            # Magnet link'i ekle
            try:
                # Yeni API: add_torrent ile magnet link ekle
                params = lt.add_torrent_params()
                params.url = self.magnet_url
                params.save_path = self.download_path
                params.storage_mode = lt.storage_mode_t(2)
                self.handle = self.ses.add_torrent(params)
            except (AttributeError, TypeError):
                # Eski API fallback
                params = {
                    'save_path': self.download_path,
                    'storage_mode': lt.storage_mode_t(2),
                }
                self.handle = lt.add_magnet_uri(self.ses, self.magnet_url, params)
            
            self.progress.emit(0, "Torrent ekleniyor, metadata bekleniyor...", 0, 0)
            
            # Torrent metadata'sını bekle
            max_wait = 120
            waited = 0
            
            # has_metadata() deprecated, status() kullan
            def has_metadata():
                try:
                    status = self.handle.status()
                    return status.has_metadata if hasattr(status, 'has_metadata') else status.state >= 3
                except:
                    # Fallback: eski API
                    try:
                        return self.handle.has_metadata()
                    except:
                        return False
            
            while not has_metadata():
                if self.stop_requested:
                    if self.handle:
                        try:
                            self.ses.remove_torrent(self.handle)
                        except:
                            pass
                    self.finished.emit(self.download_path, False)
                    return
                    
                if self.pause_requested and not self.is_paused:
                    try:
                        self.handle.pause()
                        self.is_paused = True
                        self.pause_requested = False
                        self.paused.emit()
                    except Exception:
                        pass
                    
                if self.resume_requested and self.is_paused:
                    try:
                        self.handle.resume()
                        self.is_paused = False
                        self.resume_requested = False
                        self.resumed.emit()
                    except Exception:
                        pass
                
                waited += 1
                if waited > max_wait:
                    error_msg = f"Metadata alınamadı (timeout - {max_wait}s)"
                    self.progress.emit(0, error_msg, 0, 0)
                    self.finished.emit(self.download_path, False)
                    return
                    
                self.msleep(1000)
            
            self.progress.emit(5, "Metadata alındı, indirme başlıyor...", 0, 0)
            
            # İndirme durumunu takip et
            update_counter = 0
            
            while True:
                try:
                    if self.stop_requested:
                        if self.handle:
                            try:
                                self.ses.remove_torrent(self.handle)
                            except:
                                pass
                        self.finished.emit(self.download_path, False)
                        return
                    
                    # Pause/Resume kontrolü
                    if self.pause_requested and not self.is_paused:
                        try:
                            self.handle.pause()
                            self.is_paused = True
                            self.pause_requested = False
                            self.paused.emit()
                        except Exception:
                            pass
                            
                    if self.resume_requested and self.is_paused:
                        try:
                            self.handle.resume()
                            self.is_paused = False
                            self.resume_requested = False
                            self.resumed.emit()
                        except Exception:
                            pass
                    
                    # Status al
                    try:
                        s = self.handle.status()
                    except Exception as e:
                        self.progress.emit(0, f"Status hatası: {str(e)}", 0, 0)
                        self.finished.emit(self.download_path, False)
                        return
                    
                    progress = int(s.progress * 100)
                    
                    state_str = [
                        "queued for checking",
                        "checking files",
                        "downloading metadata",
                        "downloading",
                        "finished",
                        "seeding",
                        "allocating",
                        "checking fastresume"
                    ]
                    
                    state = state_str[s.state] if s.state < len(state_str) else f"unknown({s.state})"
                    
                    if self.is_paused:
                        state = "paused"
                    
                    download_speed = s.download_rate / 1000.0  # KB/s
                    upload_speed = s.upload_rate / 1000.0  # KB/s
                    
                    status_msg = f"{state} - {progress}% - ↓{download_speed:.1f} KB/s ↑{upload_speed:.1f} KB/s"
                    self.progress.emit(progress, status_msg, download_speed, upload_speed)
                    
                    if s.state == lt.torrent_status.seeding or progress >= 100:
                        self.progress.emit(100, "İndirme tamamlandı!", 0, 0)
                        self.finished.emit(self.download_path, True)
                        return
                    
                    update_counter += 1
                    self.msleep(1000)
                    
                except Exception as e:
                    self.progress.emit(0, f"Hata: {str(e)}", 0, 0)
                    self.finished.emit(self.download_path, False)
                    return
                
        except Exception as e:
            error_msg = f"Hata: {str(e)}"
            self.progress.emit(0, error_msg, 0, 0)
            self.finished.emit(self.download_path, False)
    
    def pause(self):
        self.pause_requested = True
    
    def resume(self):
        self.resume_requested = True
    
    def stop(self):
        self.stop_requested = True


class SearchThread(QThread):
    """Arama thread'i"""
    results_ready = pyqtSignal(list)  # list of (title, url) tuples
    error = pyqtSignal(str)
    
    def __init__(self, search_query):
        super().__init__()
        self.search_query = search_query
        
    def run(self):
        try:
            # URL'yi encode et
            encoded_query = quote_plus(self.search_query)
            search_url = f"https://fitgirl-repacks.site/?s={encoded_query}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # entry-title class'ına sahip h1 elementlerini bul
            results = []
            h1_elements = soup.find_all('h1', class_='entry-title')
            
            for h1 in h1_elements:
                a_tag = h1.find('a')
                if a_tag:
                    title = a_tag.get_text(strip=True)
                    href = a_tag.get('href', '')
                    if title and href:
                        results.append((title, href))
            
            self.results_ready.emit(results)
            
        except Exception as e:
            self.error.emit(f"Arama hatası: {str(e)}")


class MagnetThread(QThread):
    """Magnet link bulma thread'i"""
    magnet_found = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, page_url):
        super().__init__()
        self.page_url = page_url
        
    def run(self):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(self.page_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # "magnet" içeren a elementlerini bul
            magnet_links = soup.find_all('a', href=re.compile(r'magnet:', re.I))
            
            if not magnet_links:
                # Alternatif olarak text içinde "magnet" geçen a elementlerini ara
                all_links = soup.find_all('a')
                for link in all_links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True).lower()
                    if 'magnet:' in href.lower():
                        magnet_links.append(link)
                        break
                    elif 'magnet' in text:
                        # Text içinden magnet linkini çıkar
                        text_content = str(link)
                        magnet_match = re.search(r'magnet:[^\s<>"]+', text_content)
                        if magnet_match:
                            self.magnet_found.emit(magnet_match.group())
                            return
            
            if magnet_links:
                magnet_url = magnet_links[0].get('href', '')
                if magnet_url:
                    self.magnet_found.emit(magnet_url)
                else:
                    self.error.emit("Magnet link bulunamadı")
            else:
                self.error.emit("Sayfada magnet link bulunamadı")
                
        except Exception as e:
            self.error.emit(f"Magnet bulma hatası: {str(e)}")


class DownloadItemWidget(QWidget):
    """İndirme öğesi için özel widget"""
    def __init__(self, download_id, parent=None):
        super().__init__(parent)
        self.download_id = download_id
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 5, 10, 5)
        self.setLayout(layout)
        
        # Üst satır: Başlık ve butonlar
        top_layout = QHBoxLayout()
        
        self.title_label = QLabel(f"İndirme #{self.download_id}")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        top_layout.addWidget(self.title_label)
        
        top_layout.addStretch()
        
        self.pause_btn = QPushButton("⏸ Durdur")
        self.pause_btn.setMaximumWidth(100)
        self.pause_btn.clicked.connect(self.on_pause_clicked)
        top_layout.addWidget(self.pause_btn)
        
        self.resume_btn = QPushButton("▶ Devam")
        self.resume_btn.setMaximumWidth(100)
        self.resume_btn.setEnabled(False)
        self.resume_btn.clicked.connect(self.on_resume_clicked)
        top_layout.addWidget(self.resume_btn)
        
        self.remove_btn = QPushButton("✕ Kaldır")
        self.remove_btn.setMaximumWidth(100)
        self.remove_btn.clicked.connect(self.on_remove_clicked)
        top_layout.addWidget(self.remove_btn)
        
        layout.addLayout(top_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Başlatılıyor...")
        self.status_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(self.status_label)
        
    def on_pause_clicked(self):
        # MainWindow'a eriş
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.pause_download(self.download_id)
        
    def on_resume_clicked(self):
        # MainWindow'a eriş
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.resume_download(self.download_id)
        
    def on_remove_clicked(self):
        # MainWindow'a eriş
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window.remove_download(self.download_id)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.download_threads = {}  # {download_id: (thread, item_widget, list_item)}
        self.download_counter = 0
        self.init_ui()
        
        if not LIBTORRENT_AVAILABLE:
            QMessageBox.warning(
                self, 
                "libtorrent Bulunamadı",
                "libtorrent kütüphanesi bulunamadı!\n\n"
                "Lütfen şu komutu çalıştırın:\n"
                "pip install python-libtorrent\n\n"
                "Eğer hala sorun yaşıyorsanız, Windows için Visual C++ Redistributable'ı yükleyin."
            )
        
    def init_ui(self):
        self.setWindowTitle("FitGirl Repacks İndirici")
        self.setGeometry(100, 100, 1000, 800)
        
        # Karanlık tema uygula
        self.apply_dark_theme()
        
        # Ana widget
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # Ana layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_widget.setLayout(main_layout)
        
        # Başlık
        title_label = QLabel("🎮 FitGirl Repacks İndirici")
        title_label.setStyleSheet("font-size: 24pt; font-weight: bold; color: #4CAF50; margin-bottom: 10px;")
        main_layout.addWidget(title_label)
        
        # Tab widget (Arama ve URL seçenekleri)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #444;
                background: #1e1e1e;
                border-radius: 5px;
            }
            QTabBar::tab {
                background: #2d2d2d;
                color: #ccc;
                padding: 10px 20px;
                margin-right: 2px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }
            QTabBar::tab:selected {
                background: #1e1e1e;
                color: #4CAF50;
                border-bottom: 2px solid #4CAF50;
            }
            QTabBar::tab:hover {
                background: #3d3d3d;
            }
        """)
        main_layout.addWidget(self.tabs)
        
        # Arama sekmesi
        search_tab = QWidget()
        search_layout = QVBoxLayout()
        search_layout.setSpacing(10)
        search_tab.setLayout(search_layout)
        
        # Arama input ve buton
        search_input_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Oyun ismi girin (örn: Red Dead Redemption 2)")
        self.search_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #2d2d2d;
                color: #fff;
                font-size: 11pt;
            }
            QLineEdit:focus {
                border: 2px solid #4CAF50;
            }
        """)
        self.search_button = QPushButton("🔍 Ara")
        self.search_button.setStyleSheet(self.get_button_style())
        self.search_button.clicked.connect(self.on_search_clicked)
        self.search_button.setMinimumHeight(40)
        search_input_layout.addWidget(self.search_input)
        search_input_layout.addWidget(self.search_button)
        search_layout.addLayout(search_input_layout)
        
        # Arama sonuçları
        search_results_label = QLabel("Arama Sonuçları:")
        search_results_label.setStyleSheet("font-weight: bold; font-size: 12pt; color: #4CAF50; margin-top: 10px;")
        search_layout.addWidget(search_results_label)
        
        self.search_results_list = QListWidget()
        self.search_results_list.setStyleSheet("""
            QListWidget {
                background: #2d2d2d;
                border: 1px solid #444;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #333;
                color: #fff;
            }
            QListWidget::item:hover {
                background: #3d3d3d;
            }
            QListWidget::item:selected {
                background: #4CAF50;
                color: #000;
            }
        """)
        self.search_results_list.itemDoubleClicked.connect(self.on_result_selected)
        search_layout.addWidget(self.search_results_list)
        
        self.tabs.addTab(search_tab, "🔍 Arama")
        
        # URL sekmesi
        url_tab = QWidget()
        url_layout = QVBoxLayout()
        url_layout.setSpacing(10)
        url_tab.setLayout(url_layout)
        
        # URL input ve buton
        url_input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("FitGirl URL'sini girin")
        self.url_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #2d2d2d;
                color: #fff;
                font-size: 11pt;
            }
            QLineEdit:focus {
                border: 2px solid #4CAF50;
            }
        """)
        self.url_button = QPushButton("⬇ İndir")
        self.url_button.setStyleSheet(self.get_button_style())
        self.url_button.clicked.connect(self.on_url_clicked)
        self.url_button.setMinimumHeight(40)
        url_input_layout.addWidget(self.url_input)
        url_input_layout.addWidget(self.url_button)
        url_layout.addLayout(url_input_layout)
        
        self.tabs.addTab(url_tab, "🔗 URL")
        
        # İndirmeler bölümü
        downloads_group = QGroupBox("📥 Aktif İndirmeler")
        downloads_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 12pt;
                color: #4CAF50;
                border: 2px solid #444;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        downloads_layout = QVBoxLayout()
        downloads_group.setLayout(downloads_layout)
        
        self.downloads_list = QListWidget()
        self.downloads_list.setStyleSheet("""
            QListWidget {
                background: #2d2d2d;
                border: 1px solid #444;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                background: #1e1e1e;
                border: 1px solid #444;
                border-radius: 5px;
                margin: 5px;
            }
        """)
        downloads_layout.addWidget(self.downloads_list)
        
        main_layout.addWidget(downloads_group)
        
        # Status bar
        self.status_label = QLabel("✅ Hazır" if LIBTORRENT_AVAILABLE else "⚠ libtorrent bulunamadı")
        self.status_label.setStyleSheet("""
            background: #2d2d2d;
            padding: 10px;
            border: 1px solid #444;
            border-radius: 5px;
            color: #fff;
            font-size: 10pt;
        """)
        main_layout.addWidget(self.status_label)
        
        # Status güncelleme timer'ı
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)  # Her saniye güncelle
        
    def apply_dark_theme(self):
        """Karanlık tema uygula"""
        dark_palette = QPalette()
        
        # Renkler
        dark_palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
        dark_palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        dark_palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
        dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 30))
        dark_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0, 0, 0))
        dark_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
        dark_palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
        dark_palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
        dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
        dark_palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        dark_palette.setColor(QPalette.ColorRole.Link, QColor(76, 175, 80))
        dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(76, 175, 80))
        dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        
        self.setPalette(dark_palette)
        
        # Genel stil
        self.setStyleSheet("""
            QMainWindow {
                background: #1e1e1e;
            }
            QWidget {
                background: #1e1e1e;
                color: #fff;
            }
            QProgressBar {
                border: 1px solid #444;
                border-radius: 5px;
                text-align: center;
                background: #2d2d2d;
                color: #fff;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #45a049);
                border-radius: 4px;
            }
        """)
    
    def get_button_style(self):
        """Buton stili"""
        return """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4CAF50, stop:1 #45a049);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 11pt;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5CBF60, stop:1 #4CAF50);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3d8f40, stop:1 #357a38);
            }
            QPushButton:disabled {
                background: #444;
                color: #888;
            }
        """
        
    def update_status(self):
        """Status'u güncelle"""
        active_downloads = len([t for t in self.download_threads.values() if t[0].isRunning()])
        if active_downloads > 0:
            self.status_label.setText(f"📥 Aktif indirme: {active_downloads}")
        else:
            if LIBTORRENT_AVAILABLE:
                self.status_label.setText("✅ Hazır")
            else:
                self.status_label.setText("⚠ libtorrent bulunamadı")
    
    def on_search_clicked(self):
        query = self.search_input.text().strip()
        if not query:
            self.status_label.setText("⚠ Lütfen bir arama terimi girin")
            return
        
        self.status_label.setText("🔍 Aranıyor...")
        self.search_button.setEnabled(False)
        self.search_results_list.clear()
        
        self.search_thread = SearchThread(query)
        self.search_thread.results_ready.connect(self.on_search_results)
        self.search_thread.error.connect(self.on_search_error)
        self.search_thread.start()
    
    def on_search_results(self, results):
        self.search_button.setEnabled(True)
        if not results:
            self.status_label.setText("❌ Sonuç bulunamadı")
            return
        
        self.status_label.setText(f"✅ {len(results)} sonuç bulundu")
        for title, url in results:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.search_results_list.addItem(item)
    
    def on_search_error(self, error_msg):
        self.search_button.setEnabled(True)
        self.status_label.setText(f"❌ {error_msg}")
    
    def on_result_selected(self, item):
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.start_download_from_url(url)
    
    def on_url_clicked(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("⚠ Lütfen bir URL girin")
            return
        
        if 'fitgirl-repacks.site' not in url:
            self.status_label.setText("⚠ Geçerli bir FitGirl URL'si girin")
            return
        
        self.start_download_from_url(url)
    
    def start_download_from_url(self, page_url):
        """URL'den magnet bul ve indirmeyi başlat"""
        self.status_label.setText("🔗 Magnet link aranıyor...")
        
        # Klasör seç
        download_dir = QFileDialog.getExistingDirectory(self, "İndirme Klasörü Seçin")
        if not download_dir:
            self.status_label.setText("❌ İndirme iptal edildi")
            return
        
        self.magnet_thread = MagnetThread(page_url)
        self.magnet_thread.magnet_found.connect(lambda magnet: self.start_download(magnet, download_dir))
        self.magnet_thread.error.connect(self.on_magnet_error)
        self.magnet_thread.start()
    
    def on_magnet_error(self, error_msg):
        self.status_label.setText(f"❌ {error_msg}")
    
    def start_download(self, magnet_url, download_path):
        """Torrent indirmeyi başlat"""
        download_id = self.download_counter
        self.download_counter += 1
        
        # İndirme thread'ini oluştur
        download_thread = DownloadThread(magnet_url, download_path, download_id)
        download_thread.progress.connect(lambda p, msg, dl, ul: self.on_download_progress(download_id, p, msg, dl, ul))
        download_thread.finished.connect(lambda path, success: self.on_download_finished(download_id, path, success))
        download_thread.paused.connect(lambda: self.on_download_paused(download_id))
        download_thread.resumed.connect(lambda: self.on_download_resumed(download_id))
        
        # İndirme widget'ını oluştur
        item_widget = DownloadItemWidget(download_id)
        
        # List item oluştur
        list_item = QListWidgetItem()
        list_item.setSizeHint(item_widget.sizeHint())
        self.downloads_list.addItem(list_item)
        self.downloads_list.setItemWidget(list_item, item_widget)
        
        self.download_threads[download_id] = (download_thread, item_widget, list_item)
        
        download_thread.start()
        self.status_label.setText(f"📥 İndirme #{download_id} başlatıldı")
    
    def on_download_progress(self, download_id, progress, status_msg, download_speed, upload_speed):
        if download_id in self.download_threads:
            _, item_widget, _ = self.download_threads[download_id]
            item_widget.progress_bar.setValue(progress)
            item_widget.status_label.setText(status_msg)
    
    def on_download_finished(self, download_id, download_path, success):
        if download_id in self.download_threads:
            _, item_widget, _ = self.download_threads[download_id]
            if success:
                item_widget.title_label.setText(f"✅ İndirme #{download_id} - Tamamlandı")
                item_widget.status_label.setText(f"Klasör: {download_path}")
                item_widget.progress_bar.setValue(100)
                item_widget.pause_btn.setEnabled(False)
                item_widget.resume_btn.setEnabled(False)
                self.status_label.setText(f"✅ İndirme #{download_id} tamamlandı")
            else:
                item_widget.title_label.setText(f"❌ İndirme #{download_id} - Başarısız")
                item_widget.status_label.setText("İndirme durduruldu veya hata oluştu")
                item_widget.pause_btn.setEnabled(False)
                item_widget.resume_btn.setEnabled(False)
                self.status_label.setText(f"❌ İndirme #{download_id} başarısız")
    
    def on_download_paused(self, download_id):
        if download_id in self.download_threads:
            _, item_widget, _ = self.download_threads[download_id]
            item_widget.pause_btn.setEnabled(False)
            item_widget.resume_btn.setEnabled(True)
    
    def on_download_resumed(self, download_id):
        if download_id in self.download_threads:
            _, item_widget, _ = self.download_threads[download_id]
            item_widget.pause_btn.setEnabled(True)
            item_widget.resume_btn.setEnabled(False)
    
    def pause_download(self, download_id):
        """İndirmeyi duraklat"""
        if download_id in self.download_threads:
            thread, _, _ = self.download_threads[download_id]
            if thread.isRunning():
                thread.pause()
    
    def resume_download(self, download_id):
        """İndirmeyi devam ettir"""
        if download_id in self.download_threads:
            thread, _, _ = self.download_threads[download_id]
            if thread.isRunning():
                thread.resume()
    
    def remove_download(self, download_id):
        """İndirmeyi kaldır"""
        if download_id in self.download_threads:
            thread, _, list_item = self.download_threads[download_id]
            if thread.isRunning():
                reply = QMessageBox.question(
                    self, 
                    "İndirmeyi Durdur",
                    "İndirme devam ediyor. Durdurmak istediğinize emin misiniz?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    thread.stop()
                    thread.wait(3000)
            
            # Listeden kaldır
            row = self.downloads_list.row(list_item)
            self.downloads_list.takeItem(row)
            del self.download_threads[download_id]
    
    def closeEvent(self, event):
        # Tüm aktif indirmeleri durdur
        active_count = len([t for t in self.download_threads.values() if t[0].isRunning()])
        if active_count > 0:
            reply = QMessageBox.question(
                self,
                "Aktif İndirmeler Var",
                f"{active_count} aktif indirme var. Kapatmak istediğinize emin misiniz?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            
            for download_id, (thread, _, _) in self.download_threads.items():
                if thread.isRunning():
                    thread.stop()
                    thread.wait(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern görünüm için
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
