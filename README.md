Руководство пользователя
Audio Recognizer AudD + AcoustID — MP3 и M4A
Инструкция по установке, настройке и запуску программы
Назначение: программа распознаёт музыкальные файлы, получает сведения о треке, записывает теги,
добавляет доступную обложку и переименовывает файл.
Форматы: MP3 (.mp3) и M4A (.m4a). Программа ищет файлы также во вложенных папках(По умолчанию включён предварительный просмотр. Чтобы записать изменения в оригинальные
файлы, добавь параметр --apply. Автоматические резервные копии не создаются.)
1. Что понадобится

• Python 3 — для запуска скрипта.

• Библиотеки requests и mutagen — для запросов к сервисам и редактирования аудиотегов.

• Chromaprint (fpcalc.exe) — обязателен для распознавания через AcoustID.

• AudD API Token — для распознавания через AudD; при запросе можно нажать Enter, чтобы пропустить AudD.

• AcoustID Application API Key — обязателен для текущей версии программы, даже если AudD отключён.

• FFmpeg — нужен для подготовки файлов больше 10 МБ перед отправкой в AudD.

1.установи Python: https://www.python.org/downloads/.


2.Скачай Chromaprint: https://acoustid.org/chromaprint/. Установи или распакуй его и добавь папку с
fpcalc.exe в PATH Windows


3.1.Открой https://dashboard.audd.io/.

3.2.Зарегистрируйся или войди в аккаунт.

3.3.Найди API Token в личном кабинете и скопируй его.

3.4.При запуске вставь токен в запрос AudD API Token.
Если AudD не нужен, при запросе токена нажми Enter. Также его можно отключить командой aud_off.


4.1.Открой https://acoustid.org/ и войди или зарегистрируйся.

4.2.Создай приложение в аккаунте, если это требуется сайтом.

4.3.Скопируй Application API Key.

4.4.Вставь ключ в запрос AcoustID при запуске программы


5.Основные команды

Предварительный просмотр

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка"

Показывает предполагаемые изменения, но не записывает теги и не переименовывает оригинальные
файлы.

Записать теги и переименовать

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --apply

Изменения вносятся непосредственно в исходные MP3/M4A. Автоматических резервных копий нет.

Отключить AudD командой aud_off

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" aud_off --apply

AudD будет пропущен. Продолжат работать AcoustID и поиск MusicBrainz по имени файла. Ключ

AcoustID всё равно потребуется.

Альтернативная команда отключения

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --aud-off --apply

Заменить существующие теги и обложки

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --apply --overwrite

Параметр --overwrite разрешает заменять уже заполненные поля и существующую обложку. Используй

его только при необходимости.

Изменить длину фрагмента для аудиоотпечатка

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --length 180

По умолчанию используется 120 секунд. Параметр --length влияет на расчёт отпечатка AcoustID, а не

изменяет продолжительность исходной песни.

Изменить минимальный балл AcoustID

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --min-score 0.60

По умолчанию порог равен 0.70 (70%). Более низкий порог может дать больше совпадений, но

повышает риск ошибочного выбора.

Указать путь к fpcalc.exe вручную

python audio_recognizer_audd_mp3_m4a.py "C:\Users\ADMIN\Desktop\музыка" --fpcalc

"C:\Tools\Chromaprint\fpcalc.exe"

Замени пример пути на фактическое расположение fpcalc.exe.

Полезные ссылки:

Python: https://www.python.org/downloads/

AudD: https://dashboard.audd.io/

AcoustID / Chromaprint: https://acoustid.org/chromaprint/

FFmpeg: https://ffmpeg.org/download.htm


Скрипт может ошибиться или же не найти песню если ее нет в базе данных так что лучше всегда перепроверить что он написал 
