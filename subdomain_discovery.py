import requests
import json
import dns.resolver
import dns.zone
import dns.query
import os
import sys
import time

def get_subdomains_from_dns(domain, nameserver="8.8.8.8", timeout=5):
    """
    Базовый метод, который пытается:
    1. Получить субдомены через AXFR (если открыт).
    2. Определить wildcard TXT-записи.
    3. Проверить MX-записи.
    Возвращает set субдоменов (без дубликатов).
    """
    subdomains = set()
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [nameserver]
        resolver.timeout = timeout
        resolver.lifetime = timeout

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

        for ns in ns_servers:
            try:
                zone_transfer = dns.query.xfr(ns, domain, timeout=timeout)
                zone = dns.zone.from_xfr(zone_transfer)
                for name, _ in zone.nodes.items():
                    subdomains.add(f"{name}.{domain}")
                if subdomains:
                    print("AXFR-зона получена (частично или полностью).")
            except dns.exception.FormError:
                pass
            except Exception as e:
                print(f"Ошибка при AXFR-запросе к {ns}: {e}")

        if not subdomains:
            print("AXFR-зона закрыта или не даёт результата. Переходим к wildcard-запросам.")

            try:
                txt_records = resolver.resolve(f"*.{domain}", "TXT")
                if txt_records:
                    print("Обнаружен wildcard через TXT.")
                    subdomains.add(f"*.{domain}")
            except Exception:
                print("Wildcard TXT-записи не найдены.")

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
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                try:
                    data = json.loads(r.text)
                    if not data:
                        print("crt.sh вернул пустой ответ")
                        break
                        
                    for entry in data:
                        name_value = entry.get('name_value', '')
                        for line in name_value.splitlines():
                            clean_name = line.replace('*.', '').lower().strip()
                            if clean_name and clean_name.endswith(domain.lower()):
                                subdomains.add(clean_name)
                    break
                except json.JSONDecodeError:
                    print("Ошибка декодирования JSON от crt.sh")
                    break
            else:
                print(f"Неожиданный код ответа от crt.sh: {r.status_code}")
                if attempt < max_retries - 1:
                    print(f"Повторная попытка через {retry_delay} сек... (попытка {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                    
        except Exception as e:
            print(f"Ошибка при запросе к crt.sh: {e}")
            if attempt < max_retries - 1:
                print(f"Повторная попытка через {retry_delay} сек... (попытка {attempt + 2}/{max_retries})")
                time.sleep(retry_delay)
    return subdomains

if __name__ == '__main__':
    domain = input("Введите домен: ").strip()

    dns_subs = get_subdomains_from_dns(domain)
    print(f"\n[DNS] Найдено {len(dns_subs)} субдоменов:")
    for s in sorted(dns_subs):
        print(f"  {s}")

    crtsh_subs = get_subdomains_from_crtsh(domain)
    print(f"\n[crt.sh] Найдено {len(crtsh_subs)} субдоменов:")
    for s in sorted(crtsh_subs):
        print(f"  {s}")

    all_subdomains = sorted(dns_subs.union(crtsh_subs))
    print(f"\nИтого уникальных субдоменов: {len(all_subdomains)}")
    for s in all_subdomains:
        print(f"  {s}")

    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    output_filename = f"{domain}.txt"
    full_path = os.path.join(script_dir, output_filename)

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            for sub in all_subdomains:
                f.write(f"{sub}\n")
        print(f"\nСписок субдоменов сохранён в файл: {full_path}")
    except Exception as e:
        print(f"Ошибка при записи в файл: {e}")
