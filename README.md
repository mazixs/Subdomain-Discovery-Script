# Subdomain Discovery Script

## Описание
Скрипт для обнаружения поддоменов с использованием:
- **DNS-запросов** (AXFR, wildcard TXT, MX-записи)
- **Поиска по сертификатам** (Certificate Transparency Logs через crt.sh)

Автор: **mazix**  
Лицензия: **GPL-3.0**  
Версия: **1.0.0**

## Особенности
- Автоматическое исключение дубликатов
- Сохранение результатов в файл `<домен>.txt`
- Поддержка пользовательских DNS-серверов
- Интеграция с crt.sh API

## Установка
1. Клонируйте репозиторий:
```sh
git clone https://github.com/mazix/Subdomain-Discovery-Script.git
cd Subdomain-Discovery-Script
```

2. Установите зависимости:
```sh
pip install -r requirements.txt
```

Или установите зависимости вручную:
```sh
pip install requests dnspython
```

3. Запустите скрипт:
```sh
python subdomain_discovery.py
```

После запуска скрипт запросит ввод домена в терминале:
```
Введите домен: example.com
```

## Принцип работы
1. Скрипт запрашивает у пользователя домен для сканирования
2. Выполняет DNS-запросы (AXFR, MX, TXT записи)
3. Проверяет сертификаты через crt.sh API
4. Объединяет результаты, удаляет дубликаты
5. Сохраняет найденные поддомены в файл `<домен>.txt`
6. Выводит результаты в консоль

## Использование
```sh
python subdomain_discovery.py
```
Введите домен при запросе:
```
Введите домен: example.com
```

Результаты сохраняются в `example.com.txt` в той же папке.

## Методы обнаружения
### DNS методы
1. **AXFR-запросы** - попытка зонного трансфера
2. **Wildcard TXT** - проверка wildcard записей
3. **MX-записи** - анализ почтовых серверов

### Certificate Transparency
- Автоматический парсинг данных с crt.sh
- Обработка wildcard сертификатов (*.example.com)

## Пример вывода
```
[DNS] Найдено 3 субдоменов:
  mail.example.com
  api.example.com
  dev.example.com

[crt.sh] Найдено 5 субдоменов:
  shop.example.com
  blog.example.com
  test.example.com
  api.example.com
  dev.example.com

Итого уникальных субдоменов: 6
Список сохранён в: example.com.txt
```

## Лицензия
Этот проект распространяется под лицензией [GPL-3.0](LICENSE).