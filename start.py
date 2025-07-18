import wx
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
            
            shutdown_event.wait(0.5)

def setup_hotkey_listener():
    """
    Настраивает и запускает слушатель клавиатуры pynput.
    """
    print("Поток слушателя клавиатуры запущен.")
    
    def on_toggle_osd():
        print("Нажата комбинация Ctrl+`. Переключение OSD.")
        osd_enabled_by_user = not osd_enabled_by_user
        if osd_enabled_by_user:
            gui_queue.put(Message(command=Command.SHOW))
        else:
            gui_queue.put(Message(command=Command.HIDE))

    def on_shutdown():
        print("Нажата комбинация Ctrl+Shift+Q. Завершение работы...")
        shutdown_event.set()
        # Отправляем команду STOP, чтобы GUI-поток тоже корректно завершился
        gui_queue.put(Message(command=Command.STOP))
        return False
    
    with keyboard.GlobalHotKeys({
        '<ctrl>+<shift>+q': on_shutdown,
        '<ctrl>+`': on_toggle_osd,
    }) as hotkey_listener:
        hotkey_listener.join()
    
    print("Поток слушателя клавиатуры завершен.")

# --- GUI на wxPython ---

class WxAppFrame(wx.Frame):
    def __init__(self, parent, title):
        # Стиль для окна без рамки, всегда наверху и прозрачного для кликов
        style = wx.NO_BORDER | wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.TRANSPARENT_WINDOW
        
        super(WxAppFrame, self).__init__(parent, title=title, style=style)

        if sys.platform == "win32":
            try:
                # Эта функция делает окно "невидимым" для стандартных API захвата экрана.
                # Это позволяет mss захватывать то, что находится ПОД нашим окном, без его скрытия.
                # WDA_EXCLUDEFROMCAPTURE = 0x00000011
                hwnd = self.GetHandle()
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            except Exception as e:
                print(f"Не удалось установить атрибут окна для исключения из захвата: {e}")

        self.sct = mss.mss()

        self.SetPosition((text_area['left'], text_area['top']))
        self.SetSize((text_area['width'], text_area['height']))

        # Настройка фона и прозрачности
        self.SetBackgroundColour(wx.Colour(0, 0, 0)) # Черный фон
        self.SetTransparent(int(255 * 0.7)) # Alpha-канал (0-255)

        # Панель для размещения виджетов
        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(0, 0, 0))

        # Текстовая метка для вывода перевода
        self.info_label = wx.StaticText(panel, label="Запуск...")
        self.info_label.SetForegroundColour(wx.Colour(255, 255, 255))

        font = wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.info_label.SetFont(font)
        self.info_label.Wrap(text_area["width"] - 20)

        # Размещение метки на панели с помощью сайзера
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.info_label, 1, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(sizer)

        # Таймер для периодической проверки очереди
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.run_work, self.timer)
        self.timer.Start(100) # Проверять каждые 100 мс

        self.Bind(wx.EVT_CLOSE, self.on_closing)

    def _capture_screen(self):
        sct_img = self.sct.grab(text_area)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return img

    def run_work(self, event):
        if shutdown_event.is_set():
            if not self.IsBeingDeleted():
                self.Close()
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

                    # На Windows SetWindowDisplayAffinity решает проблему.
                    # На других платформах (Linux/macOS) нужно временно убрать окно.
                    if sys.platform == "win32":
                        capture_and_send()
                    else:
                        is_currently_visible = self.IsShown()
                        if is_currently_visible:
                            self.Freeze()
                            self.Hide()
                            self.Thaw()
                            print("Скрыли")
                        
                        def capture_and_restore():
                            capture_and_send()
                            print("Сделали снимок")
                            if is_currently_visible:
                                self.Show()
                                print("Показали после скриншота")
                            
                        wx.CallAfter(capture_and_restore)

                case Command.STOP:
                    self.Close()
                    return

                case Command.SHOW:
                    self.info_label.SetLabel(message_dto.payload)
                    self.info_label.Wrap(text_area["width"] - 20)
                    self.Layout()
                    self.Show()
                    print("Показали")

                case Command.HIDE:
                    self.Hide()
        except queue.Empty:
            pass

    def on_closing(self, event):
        if not shutdown_event.is_set():
            print("Получен запрос на закрытие окна.")
            shutdown_event.set()
        
        self.timer.Stop()
        try: capture_queue.put_nowait(None)
        except queue.Full: pass
            
        self.Destroy()

if __name__ == "__main__":
    translator_worker = threading.Thread(target=translator_thread, daemon=True)
    translator_worker.start()
    
    hotkey_worker = threading.Thread(target=setup_hotkey_listener, daemon=True)
    hotkey_worker.start()

    refresher_worker = threading.Thread(target=refresher_thread, daemon=True)
    refresher_worker.start()

    app = wx.App(False)
    frame = WxAppFrame(None, "OSD Переводчик")
    # Не показываем окно сразу, ждем первой команды
    app.MainLoop()

    print("Программа завершена.")