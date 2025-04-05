# Команды Fly.io для управления приложением download-v1-app

## Важные пояснения по использованию команд Fly.io

### Локальные команды vs. команды внутри контейнера

1. **Команды Fly.io (`fly ...`) выполняются только на локальном компьютере**, а не внутри контейнера приложения.

2. **После подключения через SSH** (`fly ssh console -a download-v1-app`):
   - Вы находитесь внутри контейнера приложения
   - Команда `fly` недоступна внутри контейнера
   - Используйте стандартные Linux-команды: `df -h`, `ls -la`, `find`, и т.д.
   - Для выхода из SSH-сессии используйте `exit` или нажмите `Ctrl+D`

3. **Два способа выполнения команд**:
   - **Локально**: `fly ssh console -a download-v1-app -C "команда"` - выполняет команду и сразу отключается
   - **Внутри SSH-сессии**: сначала подключитесь через `fly ssh console`, затем выполняйте команды напрямую

### Примеры правильного использования

**Неправильно** (внутри SSH-сессии):
```
root@e825412f663e58:/app# fly ssh console -a download-v1-app -C "df -h"
-bash: fly: command not found
```

**Правильно** (внутри SSH-сессии):
```
root@e825412f663e58:/app# df -h
```

**Правильно** (локально):
```
fly ssh console -a download-v1-app -C "df -h"
```

## Аутентификация и настройка

```bash
# Вход в аккаунт Fly.io
fly auth login

# Выход из аккаунта
fly auth logout

# Проверка текущего пользователя
fly auth whoami
```

## Управление приложениями

```bash
# Список всех приложений
fly apps list
# Пример вывода:
# NAME                           	OWNER   	STATUS   	LATEST DEPLOY
# download-v1-app                	personal	deployed 	46m52s ago

# Информация о приложении
fly apps info download-v1-app

# Перезапуск приложения
fly apps restart download-v1-app
```

## Деплой и управление версиями

```bash
# Деплой приложения (из директории проекта)
cd /Users/v......l/Documents/DownloadV1 && fly deploy

# Отмена деплоя (откат к предыдущей версии)
fly deploy rollback

# Просмотр истории релизов
fly apps releases download-v1-app
```

## Мониторинг и логи

```bash
# Просмотр логов приложения
fly logs -a download-v1-app

# Просмотр логов с опцией следования (как tail -f)
fly logs -a download-v1-app -f

# Просмотр логов за последний час
fly logs -a download-v1-app --hours=1
```

## SSH и консоль

```bash
# Подключение к консоли приложения
fly ssh console -a download-v1-app

# Выполнение команды через SSH (проверка версии Python)
fly ssh console -a download-v1-app -C "python3 --version"

# Выполнение команды через SSH (проверка свободного места)
fly ssh console -a download-v1-app -C "df -h"
```

## Масштабирование

```bash
# Информация о текущем масштабировании
fly scale show -a download-v1-app

# Изменение размера виртуальной машины (например, до shared-cpu-2x)
fly scale vm shared-cpu-2x -a download-v1-app

# Изменение количества экземпляров (масштабирование до 2 экземпляров)
fly scale count 2 -a download-v1-app
```

## Управление секретами и переменными окружения

```bash
# Установка секрета
fly secrets set API_KEY=ваш_секретный_ключ -a download-v1-app

# Установка нескольких секретов
fly secrets set DB_USER=admin DB_PASSWORD=секретный_пароль -a download-v1-app

# Список всех секретов (только имена)
fly secrets list -a download-v1-app
```

## Управление томами (volumes)

```bash
# Список томов в вашем приложении
fly volumes list -a download-v1-app
# Пример вывода:
# ID                  	STATE  	NAME         	SIZE	REGION	ZONE	ENCRYPTED	ATTACHED VM   	CREATED AT
# vol_4987njddde9epo6v	created	downloads_new	2GB 	waw   	4f89	true     	e825412f663e58	12 hours ago

# Подробная информация о конкретном томе
fly volumes show vol_4987njddde9epo6v -a download-v1-app

# Увеличение размера тома (например, до 5 ГБ)
fly volumes extend vol_4987njddde9epo6v -a download-v1-app --size 5
```

## Проверка хранилища

```bash
# Проверка размера и использования томов
fly ssh console -a download-v1-app -C "df -h"

# Проверка использования диска для директории downloads
fly ssh console -a download-v1-app -C "du -sh /app/downloads"

# Список файлов с размерами в директории downloads
fly ssh console -a download-v1-app -C "ls -lah /app/downloads | head -n 20"

# Поиск самых больших файлов в директории downloads
fly ssh console -a download-v1-app -C "find /app/downloads -type f -name '*.mp4' -exec du -sh {} \; | sort -rh | head -n 5"
```

## Очистка хранилища

```bash
# Подключение к консоли для очистки файлов
fly ssh console -a download-v1-app

# После подключения можно удалить ненужные файлы:
# Удаление всех mp4 файлов
find /app/downloads -type f -name "*.mp4" -exec rm -f {} \;

# Удаление mp4 файлов старше 1 дня
find /app/downloads -type f -name "*.mp4" -mtime +1 -delete

# Удаление временных файлов
find /app/downloads -type f -name "*.temp.mp4" -delete

# Удаление файлов с определенным ID
find /app/downloads -name "*492ec174*" -exec rm -f {} \;
```

## Сетевые настройки

```bash
# Список IP-адресов
fly ips list -a download-v1-app
# Пример вывода:
# VERSION	IP                   	TYPE              	REGION	CREATED AT
# v6     	2a09:8280:1::6e:42c:0	public (dedicated)	global	12h44m ago
# v4     	66.241.124.194       	public (shared)   	      	Jan 1 0001 00:00

# Выделение выделенного IPv4-адреса
fly ips allocate-v4 -a download-v1-app --dedicated

# Освобождение IP-адреса
fly ips release 66.241.124.194 -a download-v1-app
```

## Мониторинг состояния

```bash
# Проверка состояния приложения
fly status -a download-v1-app

# Проверка метрик приложения
fly metrics -a download-v1-app

# Открытие приложения в браузере
fly apps open -a download-v1-app
```

## Резервное копирование данных

```bash
# Создать архив данных внутри приложения
fly ssh console -a download-v1-app -C "tar -czf /tmp/downloads_backup.tar.gz /app/downloads"

# Скачать архив на локальный компьютер
fly ssh sftp get /tmp/downloads_backup.tar.gz /Users/v....l/Documents/DownloadV1/downloads_backup.tar.gz -a download-v1-app

# Создать резервную копию только логов
fly ssh console -a download-v1-app -C "tar -czf /tmp/logs_backup.tar.gz /app/downloads/logs"
```

## Проверка и управление процессами

```bash
# Проверка запущенных процессов
fly ssh console -a download-v1-app -C "ps aux"

# Проверка использования памяти
fly ssh console -a download-v1-app -C "free -h"

# Проверка загрузки CPU
fly ssh console -a download-v1-app -C "top -b -n 1"
```

## Советы по эффективной работе с Fly.io

### Работа с файлами и директориями

```bash
# Проверка содержимого директории загрузок
fly ssh console -a download-v1-app -C "ls -la /app/downloads"

# Внутри SSH-сессии: удаление файлов старше 30 минут
find /app/downloads -type f -mmin +30 -delete

# Внутри SSH-сессии: подсчет количества файлов
find /app/downloads -type f | wc -l

# Внутри SSH-сессии: проверка прав доступа к директории
ls -ld /app/downloads
```

### Отладка проблем с дисковым пространством

```bash
# Проверка использования диска по директориям (локально)
fly ssh console -a download-v1-app -C "du -h --max-depth=1 /app"

# Внутри SSH-сессии: поиск самых больших файлов
find /app -type f -exec du -sh {} \; | sort -rh | head -n 10

# Внутри SSH-сессии: проверка свободного места
df -h
```

### Важные замечания

1. **Команды внутри SSH-сессии не сохраняются** после выхода из сессии. Если вам нужно выполнять регулярные задачи, рассмотрите возможность добавления их в скрипты или настройки приложения.

2. **Для автоматизации задач** используйте планировщик задач в вашем приложении или настройте периодические задачи через Fly.io.

3. **При работе с большими файлами** учитывайте ограничения дискового пространства и сетевой пропускной способности.

4. **Для долгосрочного хранения данных** рассмотрите возможность использования внешних хранилищ, таких как S3 или другие облачные сервисы.
