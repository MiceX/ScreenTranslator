import mss
import numpy as np
import time
import tesserocr
from PIL import Image
from argostranslate import translate
from skimage.metrics import structural_similarity as ssim

def capture_and_translate():
    """
    Захватывает область экрана, распознает текст и переводит его.
    """
    # 1. Захват экрана
    # Определите область для захвата (top, left, width, height)
    # Эти значения можно будет получать из GUI в полноценном приложении
    monitor = {"top": 860, "left": 535, "width": 840, "height": 130}

    ocr = tesserocr.PyTessBaseAPI(lang='eng')
    with mss.mss() as sct:
        last_image = None
        last_text = None

        while True:
            time.sleep(1)
            # 1. Захват экрана
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            # Сравнение с предыдущим скриншотом
            image_print = calculate_image_print(img)
            if last_image is not None:
                diff = calculate_diff(image_print, last_image)
                if diff < 1:  # Изменения менее 1%
                    continue
            last_image = image_print

            # 2. Распознавание текста
            ocr.SetImage(img)
            text = ocr.GetUTF8Text()

            if last_text:
                if text == last_text:
                    continue
            last_text = text

            if not text.strip() or len(text) < 3:
                # print(f"Текст не найден или слишком короткий (длина: {len(text)}).")
                continue

            # print(f"Распознанный текст:\n{text}")

            # 3.  Исправление и подготовка текста
            text = text.replace("|", "I").replace("/", "I").replace("1 ", "I ")  # Исправление символов
            corrected_lines = text.splitlines()

            # Объединение строк
            processed_text = ""
            for i in range(len(corrected_lines)):
                processed_text += corrected_lines[i]
                if i < len(corrected_lines) - 1 and corrected_lines[i].strip() and corrected_lines[i+1].strip():
                    processed_text += " "  # Добавляем пробел между строками, если обе не пустые

            # 4. Перевод и вывод
            translated_text = translate.translate(processed_text, "en", "ru")  # en -> ru
            # print(f"\nПеревод:\n{translated_text}")
            print(f"\n\n{translated_text}")

def calculate_image_print(img):
    return np.array(img.convert('L'))

def calculate_diff(img_print_1, img_print_2):
    score, _ = ssim(img_print_1, img_print_2, full=True)
    # Возвращаем разницу в процентах (100 - score * 100), так как SSIM показывает схожесть
    return (1 - score) * 100

if __name__ == '__main__':
    capture_and_translate()
