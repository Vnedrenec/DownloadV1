#!/bin/bash

# Создаем директорию для временных файлов
mkdir -p tmp
cd tmp

# Скачиваем ffmpeg
curl -L https://evermeet.cx/ffmpeg/ffmpeg-6.1.zip -o ffmpeg.zip

# Распаковываем
unzip ffmpeg.zip

# Создаем директорию bin если её нет
mkdir -p ../bin

# Копируем ffmpeg в bin
mv ffmpeg ../bin/

# Делаем исполняемым
chmod +x ../bin/ffmpeg

# Очищаем временные файлы
cd ..
rm -rf tmp

echo "FFmpeg успешно установлен в директорию bin"
