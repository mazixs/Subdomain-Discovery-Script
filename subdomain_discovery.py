import requests
import json
import dns.resolver
import dns.zone
import dns.query
import dns.reversename
import os
import sys


def get_subdomains_from_dns(domain, nameserver="8.8.8.8"):
    """
    Базовый метод, который пытается:
    1. Получить субдомены через AXFR (если открыт).
    2. Определить wildcard TXT-записи.
    3. Проверить MX-записи.
    Возвращает set субдоменов (без дубликатов).
    """
    subdomains = set()  # используем set, чтобы исключить дубли сразу
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [nameserver]

        # Запрашиваем записи NS для домена
        try:
            ns_records = resolver.resolve(domain, "NS")
        except dns.resolver.NoNameservers:
            print(f"Не удалось получить NS-записи для {domain}.")
            return set()
        except Exception as e:
            print(f"Ошибка при получении NS-записей: {e}")
            return set()

        ns_servers = [str(ns.target).rstrip('.') for ns in ns_records]
        print(f"NS Servers for {domain}: {ns_servers}")

        # Пробуем AXFR (зонный трансфер) для каждого NS
        for ns in ns_servers:
            try:
                zone_transfer = dns.query.xfr(ns, domain)
                zone = dns.zone.from_xfr(zone_transfer)
                for name, _ in zone.nodes.items():
                    subdomains.add(f"{name}.{domain}")
                if subdomains:
                    print("AXFR-зона получена (частично или полностью).")
            except dns.exception.FormError:
                # AXFR не разрешён
                pass
            except Exception as e:
                print(f"Ошибка при AXFR-запросе к {ns}: {e}")

        if not subdomains:
            print("AXFR-зона закрыта или не даёт результата. Переходим к wildcard-запросам.")

            # Проверка wildcard (TXT) записей
            try:
                txt_records = resolver.resolve(f"*.{domain}", "TXT")
                if txt_records:
                    print("Обнаружен wildcard через TXT.")
                    subdomains.add(f"*.{domain}")
            except Exception:
                print("Wildcard TXT-записи не найдены.")

            # Проверка MX-записей (иногда указывает на субдомены)
            try:
                mx_records = resolver.resolve(domain, "MX")
                for mx in mx_records:
                    subdomains.add(str(mx.exchange).rstrip('.'))
                if mx_records:
                    print("Обнаружены MX-записи.")
            except Exception:
                print("MX-записи не найдены.")

    except Exception as e:
        print(f"Ошибка: {e}")

    return subdomains

def get_subdomains_from_crtsh(domain):
    """
    Получаем список субдоменов, засвеченных в публичных сертификатах,
    с помощью сервиса crt.sh (Certificate Transparency Logs).
    Возвращает set субдоменов (без дубликатов).
    """
    url = f"https://crt.sh/?q={domain}&output=json"
    subdomains = set()
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = json.loads(r.text)
            for entry in data:
                name_value = entry.get('name_value', '')
                for line in name_value.splitlines():
                    clean_name = line.replace('*.', '').lower().strip()
                    if clean_name.endswith(domain.lower()):
                        subdomains.add(clean_name)
        else:
            print(f"Неожиданный код ответа от crt.sh: {r.status_code}")
    except Exception as e:
        print(f"Ошибка при запросе к crt.sh: {e}")
    return subdomains

if __name__ == '__main__':
    domain = input("Введите домен: ").strip()

    # Сначала пробуем DNS-способ
    dns_subs = get_subdomains_from_dns(domain)
    print(f"\n[DNS] Найдено {len(dns_subs)} субдоменов:")
    for s in sorted(dns_subs):
        print(f"  {s}")

    # Дополняем сертификатами
    crtsh_subs = get_subdomains_from_crtsh(domain)
    print(f"\n[crt.sh] Найдено {len(crtsh_subs)} субдоменов:")
    for s in sorted(crtsh_subs):
        print(f"  {s}")

    # Объединяем
    all_subdomains = sorted(dns_subs.union(crtsh_subs))
    print(f"\nИтого уникальных субдоменов: {len(all_subdomains)}")
    for s in all_subdomains:
        print(f"  {s}")

    # Вычисляем путь к файлу рядом со скриптом
    # Получаем путь до текущего .py
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    output_filename = f"{domain}.txt"
    full_path = os.path.join(script_dir, output_filename)

    # Записываем в файл <домен>.txt, рядом со скриптом
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            for sub in all_subdomains:
                f.write(f"{sub}\n")
        print(f"\nСписок субдоменов сохранён в файл: {full_path}")
    except Exception as e:
        print(f"Ошибка при записи в файл: {e}")
