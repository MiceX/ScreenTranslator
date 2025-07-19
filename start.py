from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
import threading
import queue
from pynput import keyboard
from enum import Enum, auto
import time
import sys
import ctypes
from dataclasses import dataclass
import mss
import numpy as np
import tesserocr
from PIL import Image
from argostranslate import translate
from skimage.metrics import structural_similarity as ssim

# Определяем возможные команды для GUI
class Command(Enum):
    SHOW = auto() # Показать/обновить текст
    HIDE = auto() # Спрятать окно
    STOP = auto() # Остановить приложение
    REQUEST_CAPTURE = auto() # Запрос от worker'а к GUI на захват экрана

@dataclass
class Message:
    command: Command
    payload: str = ""

# --- Глобальные объекты для межпоточного взаимодействия ---

# Очередь для передачи данных от потока-обработчика в GUI
gui_queue = queue.Queue()

# Очередь для передачи захваченного изображения от GUI к worker'у
capture_queue = queue.Queue(maxsize=1)

# Событие для сигнала о завершении работы всем потокам
shutdown_event = threading.Event()

osd_enabled_by_user = True

# Область для распознавания
text_area = {"top": 865, "left": 535, "width": 840, "height": 130}

def calculate_image_print(img):
    """Конвертирует изображение в Ч/Б для сравнения."""
    return np.array(img.convert('L'))

def calculate_diff(img_print_1, img_print_2):
    """Сравнивает два изображения и возвращает процент различия."""
    if img_print_1.shape != img_print_2.shape:
        return 100.0 # Если размеры не совпадают, считаем их полностью разными
    (score, diff) = ssim(img_print_1, img_print_2, full=True)
    # SSIM показывает схожесть, мы возвращаем разницу
    return (1 - score) * 100

# --- Функции потоков ---
def translator_thread():
    """
    Поток, который выполняет основную работу по распознаванию и переводу.
    """
    print("Поток-обработчик запущен.")
    try:
        ocr = tesserocr.PyTessBaseAPI(lang='eng')
        last_image_print = None
        last_text = None

        while not shutdown_event.is_set():
            try:
                # Ждать, пока GUI-поток сделает снимок и положит его в очередь
                img = capture_queue.get(block=True, timeout=0.5)
                # При закрытии GUI отправляет None в очередь, чтобы "разбудить" этот поток.
                # Получив None, мы должны корректно завершить цикл.
                if img is None:
                    break
            except queue.Empty:
                continue

            # рабираем всю очередь если есть и обрабатываем только последнее изображение
            while True:
                try:
                    img = capture_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Сравнение с предыдущим снимком
            current_image_print = calculate_image_print(img)
            if last_image_print is not None:
                diff = calculate_diff(current_image_print, last_image_print)
                if diff < 1:  # Порог изменения 1%
                    continue
            last_image_print = current_image_print

            # Распознавание текста
            ocr.SetImage(img)
            text = ocr.GetUTF8Text()

            # Проверка и обработка текста
            if not text.strip() or len(text.strip()) < 3:
                last_text = None
                gui_queue.put(Message(command=Command.HIDE))
                continue

            if text == last_text:
                continue
            last_text = text

            # Исправление и подготовка текста
            processed_text = text.replace("|", "I").replace("\n", " ").strip()

            # Перевод
            translated_text = translate.translate(processed_text, "en", "ru")

            # Отправка результата в GUI
            gui_queue.put(Message(command=Command.SHOW, payload=translated_text))
    except Exception as e:
        print(f"Критическая ошибка в рабочем потоке: {e}")
        shutdown_event.set()
        gui_queue.put(Message(command=Command.STOP))

    print("Поток-обработчик завершен.")
    
def refresher_thread():
        while not shutdown_event.is_set():
            if osd_enabled_by_user:
                gui_queue.put(Message(command=Command.REQUEST_CAPTURE))
            
            shutdown_event.wait(1)

def setup_hotkey_listener():
    """
    Настраивает и запускает слушатель клавиатуры pynput.
    """
    print("Поток слушателя клавиатуры запущен.")
    
    def on_toggle_osd():
        global osd_enabled_by_user
        print("Нажата комбинация Ctrl+`. Переключение OSD.")
        osd_enabled_by_user = not osd_enabled_by_user
        if osd_enabled_by_user:
            # Команда SHOW без payload просто покажет окно с последним текстом
            gui_queue.put(Message(command=Command.SHOW, payload=None))
        else:
            gui_queue.put(Message(command=Command.HIDE))

    def on_shutdown():
        print("Нажата комбинация для завершения работы. Завершение работы...")
        # Устанавливаем событие, только если оно еще не установлено,
        # чтобы избежать многократной отправки команды STOP.
        if not shutdown_event.is_set():
            shutdown_event.set()
            # Отправляем команду STOP, чтобы GUI-поток тоже корректно завершился
            gui_queue.put(Message(command=Command.STOP))
    
    with keyboard.GlobalHotKeys({
        '<ctrl>+<shift>+<f10>': on_shutdown,
        '<ctrl>+`': on_toggle_osd,
    }) as hotkey_listener:
        shutdown_event.wait()
    
    print("Поток слушателя клавиатуры завершен.")

# --- GUI на PySide6 ---

class PySideFrame(QWidget):
    
    def __init__(self):
        super().__init__()

        # Настройка флагов окна
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |    # Окно без рамки
            Qt.WindowType.WindowDoesNotAcceptFocus |
            Qt.WindowType.WindowTransparentForInput | # Прозрачность для кликов мыши
            Qt.WindowType.SplashScreen |
            Qt.WindowType.WindowStaysOnTopHint |   # Поверх всех окон
            Qt.WindowType.Tool                   # Не показывать в панели задач
        )
        # Атрибут для поддержки полной прозрачности фона
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Исключение окна из захвата экрана на Windows
        if sys.platform == "win32":
            try:
                hwnd = self.winId()
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            except Exception as e:
                print(f"Не удалось установить атрибут окна для исключения из захвата: {e}")

        self.sct = mss.mss()

        # Установка геометрии и стилей
        self.setGeometry(text_area['left'], text_area['top'], text_area['width'], text_area['height'])
        self.setStyleSheet(f"""
            background-color: rgba(0, 0, 0, 70%);
            color: white;
        """)

        # Текстовая метка для вывода
        self.info_label = QLabel("Запуск...", self)
        font = QFont()
        font.setPointSize(16)
        self.info_label.setFont(font)
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Убираем любые возможные внутренние отступы у самой метки
        self.info_label.setStyleSheet("padding: 5px; margin: 0px; border: none;")

        # Размещение метки с помощью layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.setContentsMargins(0, 0, 0, 0) # Убираем внутренние отступы у layout
        self.setLayout(layout)
        
        # Таймер для проверки очереди
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_work)
        self.timer.start(100) # Проверять каждые 100 мс

    def _capture_screen(self):
        sct_img = self.sct.grab(text_area)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return img

    def run_work(self):
        if shutdown_event.is_set():
            # Явно завершаем приложение. self.close() может быть недостаточно надежным из-за флагов окна.
            QApplication.instance().quit()
            return
            
        try:
            message_dto: Message = gui_queue.get_nowait()

            match message_dto.command:
                case Command.REQUEST_CAPTURE:

                    def capture_and_send():
                        img = self._capture_screen()
                        try:
                            capture_queue.put_nowait(img)
                        except queue.Full: pass

                    if sys.platform == "win32" or not self.isVisible():
                        capture_and_send()
                    else:
                        self.info_label.hide()
                                                
                        def capture_after_hide():
                            capture_and_send()
                            self.info_label.show()
                            
                        QTimer.singleShot(50, capture_after_hide)

                case Command.STOP:
                    # Явно завершаем приложение по команде STOP.
                    QApplication.instance().quit()
                    return

                case Command.SHOW:
                    if message_dto.payload is not None:
                        self.info_label.setText(message_dto.payload)
                    self.show()

                case Command.HIDE:
                    self.hide()
        except queue.Empty:
            pass

    def closeEvent(self, event):
        if not shutdown_event.is_set():
            print("Получен запрос на закрытие окна.")
            shutdown_event.set()
        
        self.timer.stop()
        try: capture_queue.put_nowait(None)
        except queue.Full: pass
        
        event.accept()

if __name__ == "__main__":
    translator_worker = threading.Thread(target=translator_thread, daemon=True)
    translator_worker.start()
    
    hotkey_worker = threading.Thread(target=setup_hotkey_listener, daemon=True)
    hotkey_worker.start()

    refresher_worker = threading.Thread(target=refresher_thread, daemon=True)
    refresher_worker.start()

    app = QApplication(sys.argv)
    frame = PySideFrame()
    # Не показываем окно сразу, ждем первой команды
    exit_code = app.exec()

    # После завершения GUI-цикла, дожидаемся корректного завершения всех потоков.
    # shutdown_event уже должен быть установлен к этому моменту.
    print("GUI завершен. Ожидание завершения рабочих потоков...")
    translator_worker.join()
    refresher_worker.join()
    hotkey_worker.join()

    print("Программа завершена.")
    sys.exit(exit_code)