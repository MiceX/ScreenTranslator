import tkinter as tk
from tkinter import font
import threading
import queue
from pynput import keyboard
import ctypes  # Добавляем импорт ctypes

class OsdApp:
    """Класс для управления окном OSD в главном потоке."""
    
    # Принимаем stop_event в конструкторе
    def __init__(self, q, stop_event):
        self.queue = q
        self.stop_event = stop_event # Сохраняем событие

        ctypes.windll.user32.SetProcessDPIAware()
        self.root = tk.Tk()
        self.root.title("OSD")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-disabled", True)

        # Прозрачность для Windows и Linux
        if self.root.tk.call('tk', 'windowingsystem') == 'win32':
            self.root.wm_attributes('-alpha', 0.7)
            self.bg_color = "black"
        else:
            self.root.attributes('-alpha', 0.7)
            self.bg_color = "#333333"

        default_font = font.Font(name="TkDefaultFont", exists=True)
        default_font.configure(size=14, weight="bold")

        self.label = tk.Label(
            self.root,
            text="Инициализация...",
            font=default_font,
            fg="white",
            bg=self.bg_color,
            wraplength=350,
            justify=tk.LEFT
        )
        self.label.pack(fill=tk.X, ipadx=20, ipady=15)

        self.center_window()
        self.process_queue()

    def center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def process_queue(self):
        try:
            message = self.queue.get_nowait()            
            self.label.config(text=message)
        except queue.Empty:
            pass
        finally:
            if self.stop_event.is_set():  # Оставляем только проверку по событию
                self.shutdown()  
            else:
                self.root.after(100, self.process_queue)

    # Метод для безопасного закрытия
    def shutdown(self):
        if self.stop_event.is_set(): # Чтобы избежать повторного вызова
             # Проверяем, существует ли еще окно
            if hasattr(self, 'root') and self.root:  # Проверяем наличие атрибута и его значение
                self.root.destroy() # Уничтожаем окно
                self.root = None      # Устанавливаем в None, чтобы пометить окно как уничтоженное

        print("GUI: закрытие.")

    def run(self):
        self.root.mainloop()

# worker_task теперь принимает событие `stop_event`
def worker_task(q, stop_event):
    print("Рабочий поток: начал работу.")
    
    # Вместо sleep используем wait, чтобы поток можно было прервать
    for i in range(10):
        if stop_event.wait(2):
            break

        q.put(f"Этап {i+1} из 10")
        print(f"Рабочий поток: отправил обновление статуса - {i+1}.")

    if not stop_event.is_set(): # Если нас не прервали
        q.put("destroy") # Отправляем команду на закрытие
    
    print("Рабочий поток: корректно завершён.")

# <-- 2. Новый компонент - глобальный слушатель клавиатуры
def global_ctrl_tilde_listener(stop_event_ref):
    """Слушает нажатие Ctrl+~ в фоне и устанавливает событие stop_event."""
    def on_activate():  # Функция, вызываемая при нажатии Ctrl+~
        print("Обнаружено нажатие Ctrl+~! Отправляю сигнал на завершение...")
        stop_event_ref.set()

    print("Инициализируем слушатель.")

    hotkey = keyboard.HotKey(
        keyboard.HotKey.parse('<ctrl>+`'),  # Все клавиши в одной строке
        on_activate
    )
    with keyboard.Listener(on_press=hotkey.press, on_release=hotkey.release) as listener:
        print("Слушатель активирован.")
        listener.join()

    print("Слушатель клавиатуры завершил работу.")

if __name__ == "__main__":
    msg_queue = queue.Queue()
    stop_event = threading.Event() # Создаем событие

    # Передаем событие в рабочий поток
    worker = threading.Thread(target=worker_task, args=(msg_queue, stop_event))
    worker.start()

    # Поток-демон автоматически завершится, когда закроется основная программа
    stop_key_listener_thread = threading.Thread(target=global_ctrl_tilde_listener, args=(stop_event,), daemon=True)
    stop_key_listener_thread.start() # Запускаем слушатель

    print("Главный поток: запускаю GUI. Нажмите Ctrl+~ для выхода.")
    # Передаем событие в приложение GUI
    app = OsdApp(msg_queue, stop_event)
    app.run() # mainloop блокирует здесь

    # После того как окно закрыто (mainloop завершён), ждём завершения рабочего потока
    print("Главный поток: жду завершения рабочего потока...")
    worker.join()

    print("Главный поток: программа полностью завершена.")