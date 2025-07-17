import tkinter as tk
from tkinter import font
import threading
import queue
from pynput import keyboard
from enum import Enum, auto
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
    TOGGLE_OSD = auto() # Переключить видимость OSD

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

refresh_time = 1.0

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
def worker_thread():
    """
    Поток, который выполняет основную работу по захвату, распознаванию и переводу.
    """
    print("Поток-обработчик запущен.")
    try:
        ocr = tesserocr.PyTessBaseAPI(lang='eng')
        last_image_print = None
        last_text = None
        first_time = True

        while not shutdown_event.is_set():
            # Пауза перед следующим циклом
            if not first_time:
                if shutdown_event.wait(refresh_time):
                    break
            else:
                first_time = False

            # 1. Запросить у GUI-потока сделать снимок
            gui_queue.put(Message(command=Command.REQUEST_CAPTURE))

            # 2. Ждать, пока GUI-поток сделает снимок и положит его в очередь
            img = capture_queue.get()

            # Если получили None, это сигнал о завершении
            if img is None:
                break
            
            # 3. Сравнение с предыдущим снимком
            current_image_print = calculate_image_print(img)
            if last_image_print is not None:
                diff = calculate_diff(current_image_print, last_image_print)
                if diff < 1:  # Порог изменения 1%
                    continue
            last_image_print = current_image_print

            # 4. Распознавание текста
            ocr.SetImage(img)
            text = ocr.GetUTF8Text()

            # 5. Проверка и обработка текста
            if not text.strip() or len(text.strip()) < 3:
                last_text = None
                gui_queue.put(Message(command=Command.HIDE))
                continue

            if text == last_text:
                continue
            last_text = text

            # 6. Исправление и подготовка текста
            processed_text = text.replace("|", "I").replace("\n", " ").strip()

            # 7. Перевод
            translated_text = translate.translate(processed_text, "en", "ru")

            # 8. Отправка результата в GUI
            gui_queue.put(Message(command=Command.SHOW, payload=translated_text))
    except Exception as e:
        print(f"Критическая ошибка в рабочем потоке: {e}")
        shutdown_event.set()
        gui_queue.put(Message(command=Command.STOP))

    print("Поток-обработчик завершен.")


def setup_hotkey_listener():
    """
    Настраивает и запускает слушатель клавиатуры pynput.
    """
    print("Поток слушателя клавиатуры запущен.")
    
    def on_toggle_osd():
        """
        Эта функция вызывается при нажатии Ctrl+` для переключения OSD.
        """
        print("Нажата комбинация Ctrl+`. Переключение OSD.")
        gui_queue.put(Message(command=Command.TOGGLE_OSD))

    def on_shutdown():
        """
        Эта функция вызывается при нажатии Ctrl+Shift+Q для завершения работы.
        """
        print("Нажата комбинация Ctrl+Shift+Q. Завершение работы...")
        shutdown_event.set()
        gui_queue.put(Message(command=Command.STOP))
        return False
    
    with keyboard.GlobalHotKeys({
        '<ctrl>+<shift>+q': on_shutdown,
        '<ctrl>+`': on_toggle_osd,
    }) as hotkey_listener:
        hotkey_listener.join()
    
    print("Поток слушателя клавиатуры завершен.")


# --- Функции для GUI ---

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.sct = mss.mss() # Контекст для захвата экрана
        self.title("OSD Переводчик") # Более подходящее название

        # Флаг, управляемый пользователем через горячую клавишу
        self.osd_enabled_by_user = True
        
        # Удаляем рамку и делаем окно без рамки (как в osd_test.py)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True) # Всегда сверху
        self.wm_attributes("-disabled", True) # Прозрачность для кликов

        # Настраиваем прозрачность (по аналогии с osd_test.py)
        if self.tk.call('tk', 'windowingsystem') == 'win32':
            self.wm_attributes('-alpha', 0.7)
            self.bg_color = "black"
        else:
            self.attributes('-alpha', 0.7)
            self.bg_color = "#333333"

        # Используем шрифт по умолчанию, но делаем его жирным
        default_font = tk.font.Font(name="TkDefaultFont", exists=True)
        default_font.configure(size=16, weight="normal")
        
        # Создаем Label для отображения текста
        self.info_label = tk.Label(
            self,
            text="Запуск...", # Начальный текст
            font=default_font,
            fg="white",
            bg=self.bg_color,
            wraplength=text_area["width"] - 10, # Ширина окна минус горизонтальные отступы
            justify=tk.LEFT,  # Выравнивание строк текста между собой по левому краю
            anchor="nw"       # Размещение блока текста в левом верхнем углу виджета
        )
        self.info_label.pack(
            fill=tk.BOTH,     # Заполнение по горизонтали и вертикали
            expand=True,      # Разрешить виджету занимать все доступное пространство
            ipadx=5, ipady=0  # Внутренние отступы
        )

        # Размещаем окно в указанной области
        self.geometry(
            f"{text_area['width']}x{text_area['height']}+"
            f"{text_area['left']}+{text_area['top']}"
        )
        # Привязываем закрытие окна к нашей функции
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Запускаем периодическую проверку очереди
        self.check_queue()

    def _capture_screen(self):
        """Делает снимок указанной области и возвращает его как объект PIL.Image."""
        sct_img = self.sct.grab(text_area)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return img

    def check_queue(self):
        """
        Проверяет очередь gui_queue на наличие новых DTO
        и обновляет GUI в соответствии с командой.
        """
        try:
            message_dto: Message = gui_queue.get_nowait()

            if message_dto.command == Command.REQUEST_CAPTURE:
                # Скрываем окно на время снимка, если оно видимо
                is_currently_visible = self.state() == 'normal'
                if is_currently_visible:
                    self.withdraw() # Скрываем окно
                    self.update_idletasks() # Принудительно обновляем GUI

                img = self._capture_screen()

                if is_currently_visible:
                    self.deiconify() # Показываем окно обратно

                # Отправляем изображение worker'у, не блокируя GUI
                try:
                    capture_queue.put_nowait(img)
                except queue.Full:
                    pass # Worker занят, пропускаем этот кадр

            if message_dto.command == Command.STOP:
                self.on_closing()
                return # Больше не планируем проверку

            if message_dto.command == Command.TOGGLE_OSD:
                self.osd_enabled_by_user = not self.osd_enabled_by_user
                if self.osd_enabled_by_user:
                    self.deiconify()
                else:
                    self.withdraw()
                print(f"OSD {'включен' if self.osd_enabled_by_user else 'выключен'} по горячей клавише.")

            elif message_dto.command == Command.SHOW:
                # Отображаем, только если OSD включен пользователем
                if self.osd_enabled_by_user:
                    self.info_label.config(text=message_dto.payload)
                    self.deiconify()
            elif message_dto.command == Command.HIDE:
                if self.osd_enabled_by_user:
                    self.withdraw()

        except queue.Empty:
            # Очередь пуста, ничего не делаем
            pass
        
        if not shutdown_event.is_set():
            self.after(100, self.check_queue)

    def on_closing(self):
        """
        Вызывается при закрытии окна или нажатии Ctrl+~.
        """
        if not shutdown_event.is_set():
            print("Получен запрос на закрытие окна.")
            # Устанавливаем событие, чтобы все потоки завершились
            shutdown_event.set()

        # Разблокируем worker_thread, если он ждет изображение в очереди
        try:
            capture_queue.put_nowait(None)
        except queue.Full:
            # Если очередь полна, значит worker еще не забрал предыдущий кадр.
            # Он увидит shutdown_event, как только освободится.
            pass

        self.after(200, self.destroy)

# --- Точка входа в программу ---

if __name__ == "__main__":
    # 1. Создаем и запускаем поток-обработчик
    worker = threading.Thread(target=worker_thread, daemon=True)
    worker.start()
    
    # 2. Создаем и запускаем поток-слушатель клавиатуры
    listener = threading.Thread(target=setup_hotkey_listener, daemon=True)
    listener.start()

    # 3. Создаем и запускаем GUI в основном потоке
    app = App()
    app.mainloop()

    # После выхода из mainloop программа завершится.
    # daemon=True гарантирует, что фоновые потоки не помешают выходу.
    print("Программа завершена.")
