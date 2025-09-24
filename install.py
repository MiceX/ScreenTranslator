import argostranslate.package
import argostranslate.translate
import requests

url = 'https://github.com/tesseract-ocr/tessdata_best/raw/refs/heads/main/eng.traineddata'
filename = "eng.traineddata"

try:
    response = requests.get(url, stream=True)
    response.raise_for_status()  # Проверить, что запрос успешен

    with open(filename, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Файл {filename} успешно скачан.")

except requests.exceptions.RequestException as e:
    print(f"Ошибка при скачивании файла: {e}")

from_code = "en"
to_code = "ru"

# Download and install Argos Translate package
argostranslate.package.update_package_index()
available_packages = argostranslate.package.get_available_packages()
available_package = list(
    filter(
        lambda x: x.from_code == from_code and x.to_code == to_code, available_packages
    )
)[0]
download_path = available_package.download()
argostranslate.package.install_from_path(download_path)

# Translate
installed_languages = argostranslate.translate.get_installed_languages()
from_lang = list(filter(
        lambda x: x.code == from_code,
        installed_languages))[0]
to_lang = list(filter(
        lambda x: x.code == to_code,
        installed_languages))[0]
translation = from_lang.get_translation(to_lang)
translatedText = translation.translate("Hello World!")
print(translatedText)