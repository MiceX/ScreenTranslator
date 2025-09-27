import sys
import threading
import queue
from pynput import keyboard
from enum import Enum, auto
import wx
import ctypes
import re
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
osd_window_is_visible = True # Отвечает за видимость окна OSD

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
            if not osd_window_is_visible:
                continue

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
            processed_text = text.replace("\n", " ").strip()
            # Заменяем '1' на 'I', если перед ним не цифра, а после - не цифра и не точка.
            processed_text = re.sub(r'(?<!\d)1(?![.\d])', 'I', processed_text)
            processed_text = re.sub(r'(^|\s|[.,!?;()-])([/|])', r'\1I', processed_text)
            processed_text = re.sub(r'([/|])', 'l', processed_text)

            try:
                # Перевод
                translated_text = translate.translate(processed_text, "en", "ru")
            except Exception as e:
                translated_text = processed_text
                continue

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
    
    single_press_timer = None
    
    def perform_single_press_action():
        """Действие для одиночного нажатия: переключение режима обновления."""
        global osd_enabled_by_user
        osd_enabled_by_user = not osd_enabled_by_user
        print(f"Одиночное нажатие: авто-обновление {'ВКЛЮЧЕНО' if osd_enabled_by_user else 'ВЫКЛЮЧЕНО'}.")

    def on_toggle_osd():
        nonlocal single_press_timer
        global osd_window_is_visible

        # Если таймер жив, значит, это второе нажатие (двойное)
        if single_press_timer and single_press_timer.is_alive():
            single_press_timer.cancel()
            single_press_timer = None
            
            # Действие для двойного нажатия: переключение видимости окна
            osd_window_is_visible = not osd_window_is_visible
            print(f"Двойное нажатие: OSD {'ПОКАЗАНО' if osd_window_is_visible else 'СКРЫТО'}.")
            if osd_window_is_visible:
                gui_queue.put(Message(command=Command.SHOW, payload=None))
            else:
                gui_queue.put(Message(command=Command.HIDE))
        else:
            # Первое нажатие: запускаем таймер, который выполнит действие для одиночного нажатия
            single_press_timer = threading.Timer(0.5, perform_single_press_action) # 500ms
            single_press_timer.start()

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

# --- GUI on wxPython ---

class WxFrame(wx.Frame):
    def __init__(self):
        # DPI awareness
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception as e:
                print(f"Could not set DPI awareness: {e}")

        # Frame style for an OSD window
        style = (
            wx.CLIP_CHILDREN |
            wx.STAY_ON_TOP |
            wx.FRAME_NO_TASKBAR |
            wx.NO_BORDER |
            # wx.FRAME_SHAPED |
            wx.TRANSPARENT_WINDOW
        )

        super().__init__(None, title="OSD", style=style)

        # Make window click-through on Windows, similar to tkinter's `-disabled` attribute.
        # This allows mouse events to "fall through" the window.
        if sys.platform == "win32":
            hwnd = self.GetHandle()
            extended_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20) # GWL_EXSTYLE
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, extended_style | 0x00000020) # WS_EX_TRANSPARENT

        self.SetSize(text_area['width'], text_area['height'])
        self.SetPosition((text_area['left'], text_area['top']))

        # Transparency
        self.SetTransparent(int(255 * 0.7))
        
        # Background color
        self.bg_color = wx.Colour("black")
        self.SetBackgroundColour(self.bg_color)

        # Attempt to exclude from screen capture on Windows
        self.win32_capture_mode = False
        if sys.platform == "win32":
            try:
                hwnd = self.GetHandle()
                # WDA_EXCLUDEFROMCAPTURE = 0x00000011
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
                self.win32_capture_mode = True
            except Exception as e:
                print(f"Не удалось установить атрибут окна для исключения из захвата: {e}")

        self.sct = mss.mss()

        # Main panel and sizer
        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(self.bg_color)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        # Info Label
        self.info_label = wx.StaticText(
            self.panel,
            label="Запуск...",
            style=wx.ALIGN_LEFT | wx.ST_NO_AUTORESIZE
        )
        self.info_label.SetForegroundColour(wx.Colour("white"))
        
        self.sizer.Add(self.info_label, 1, wx.EXPAND | wx.ALL, 10)
        self.panel.SetSizer(self.sizer)
        
        self.set_text_and_adjust_font("Запуск...")

        self.Bind(wx.EVT_CLOSE, self.on_closing)

        # Timer for processing queue
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.run_work, self.timer)
        self.timer.Start(100) # Poll every 100ms

    def set_text_and_adjust_font(self, text):
        # найти в text последовательность из более чем трех одинаковых символов и оставить только три таких символа
        text = re.sub(r'(\w)\1{3,}', r'\1\1\1', text)
        words = text.split(' ')
        new_words = []
        for word in words:
            if len(word) > 30:
                new_word = ' '.join([word[i:i+30] for i in range(0, len(word), 30)])
                new_words.append(new_word)
            else:
                new_words.append(word)
        text = ' '.join(new_words)

        f = wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.info_label.SetFont(f)
        
        self.info_label.SetLabel(text)
        
        return
    
        # надо переработать подбор размера шрифта

        max_font_size = 16
        min_font_size = 8

        # panel_width, _ = self.GetClientSize()
        target_width, target_height = self.panel.GetSize()
        target_width -= 20
        target_height -= 20

        for size in range(max_font_size, min_font_size - 1, -1):
            f = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            self.info_label.SetFont(f)
            
            self.info_label.SetLabel(text)
            self.info_label.Wrap(target_width)
            self.panel.Layout()

            # После того как Wrap() и Layout() отработали,
            # GetSize() вернет реальную высоту, которую занял виджет.
            # Это самый надежный способ узнать высоту текста с переносами.
            req_height = self.info_label.GetBestSize().GetHeight()
            
            print(f'Высота {req_height}px <= {target_height}px. Размер шрифта {size}')
            if req_height <= target_height:
                break
        
        self.Layout()

    def _capture_screen(self):
        try:
            sct_img = self.sct.grab(text_area)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            return img
        except Exception as e:
            print(f"Ошибка при захвате экрана: {e}")
            return None

    def run_work(self, event):
        if shutdown_event.is_set():
            if not self.IsBeingDeleted():
                self.shutdown()
            return

        try:
            message_dto: Message = gui_queue.get_nowait()

            match message_dto.command:
                case Command.REQUEST_CAPTURE:
                    def capture_and_send():
                        img = self._capture_screen()
                        if img is None:
                            return
                        try:
                            capture_queue.put_nowait(img)
                        except queue.Full:
                            pass

                    if self.win32_capture_mode or not self.IsShown():
                        capture_and_send()
                    else:
                        self.SetTransparent(0)

                        def capture_after_hide():
                            capture_and_send()
                            if osd_window_is_visible:
                                self.SetTransparent(int(255 * 0.7))

                        wx.CallLater(50, capture_after_hide)

                case Command.STOP:
                    self.shutdown()
                    return

                case Command.SHOW:
                    if message_dto.payload is not None:
                        self.set_text_and_adjust_font(message_dto.payload)
                    if osd_window_is_visible:
                        self.Show()

                case Command.HIDE:
                    self.Hide()
        except queue.Empty:
            pass

    def on_closing(self, event):
        if not shutdown_event.is_set():
            print("Получен запрос на закрытие окна.")
            shutdown_event.set()
        self.shutdown()

    def shutdown(self):
        print("GUI: закрытие.")
        if self.timer.IsRunning():
            self.timer.Stop()
        try:
            capture_queue.put_nowait(None)
        except queue.Full:
            pass
        
        if not self.IsBeingDeleted():
            wx.CallAfter(self.Destroy)

if __name__ == "__main__":
    translator_worker = threading.Thread(target=translator_thread, daemon=True)
    translator_worker.start()
    
    hotkey_worker = threading.Thread(target=setup_hotkey_listener, daemon=True)
    hotkey_worker.start()

    refresher_worker = threading.Thread(target=refresher_thread, daemon=True)
    refresher_worker.start()

    app = wx.App(False)
    gui_app = WxFrame()
    gui_app.Hide()
    app.MainLoop()
    exit_code = 0

    # После завершения GUI-цикла, дожидаемся корректного завершения всех потоков.
    print("GUI завершен. Ожидание завершения рабочих потоков...")
    translator_worker.join()
    refresher_worker.join()
    hotkey_worker.join()

    print("Программа завершена.")
    sys.exit(exit_code)