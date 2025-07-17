import time
import cv2
import mss
import numpy as np

def detect_text_with_mser():
    """
    Делает скриншот, находит текстовые блоки с помощью MSER, выводит их координаты
    и отображает результат визуально.
    """
    print("Скрипт запущен. Ожидание 2 секунды перед снимком экрана...")
    time.sleep(2)
    print("Делаю скриншот...")

    with mss.mss() as sct:
        # Захватываем весь экран (основной монитор)
        monitor = sct.monitors[1]
        sct_img = sct.grab(monitor)

        # Конвертируем изображение из формата mss в формат OpenCV (numpy array)
        # mss захватывает в BGRA, поэтому конвертируем в BGR для отображения
        # и в серый для анализа.
        img_bgr = np.array(sct_img)
        
        # Конвертируем в оттенки серого, так как MSER работает с одноканальными изображениями
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGRA2GRAY)

    print("Ищу текстовые блоки с помощью MSER...")
    
    # Создаем объект MSER. Параметры можно тонко настраивать,
    # но для начала используем значения по умолчанию.
    mser = cv2.MSER_create()
    
    # Находим регионы на изображении.
    # detectRegions возвращает список контуров регионов и их ограничивающие рамки
    regions, bboxes = mser.detectRegions(gray)

    print(f"\nНайдено {len(bboxes)} потенциальных текстовых блоков.")
    print("Координаты (x, y, ширина, высота):")
    
    # Проходим по всем найденным рамкам и выводим их координаты
    for i, box in enumerate(bboxes):
        x, y, w, h = box
        print(f"  Блок {i+1}: ({x}, {y}, {w}, {h})")
        # Рисуем прямоугольник вокруг каждого найденного блока на цветном изображении
        cv2.rectangle(img_bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)

    # Показываем изображение с выделенными блоками
    cv2.imshow("Обнаруженные текстовые блоки (MSER)", img_bgr)
    print("\nНажмите любую клавишу в окне с изображением, чтобы закрыть его.")
    cv2.waitKey(0)
    cv2.destroyAllWindows()

if __name__ == '__main__':
    detect_text_with_mser()