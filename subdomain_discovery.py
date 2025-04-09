import json
import dns.resolver
import dns.zone
# Use async versions
import dns.asyncresolver
import dns.asyncquery
import os
import sys
import time # Keep for potential sync parts, though asyncio.sleep is preferred
import argparse
import asyncio
import aiohttp # For async HTTP requests
import platform # Added for Windows event loop fix

# --- Argument Parsing (remains synchronous) ---
def parse_arguments():
    parser = argparse.ArgumentParser(description="Discover subdomains using DNS and crt.sh (async version).")
    parser.add_argument("domain", help="The domain to scan.")
    parser.add_argument("-o", "--output", help="Output file path (default: <domain>.txt).")
    parser.add_argument("-r", "--resolvers", default="8.8.8.8",
                        help="Comma-separated list of DNS resolvers (default: 8.8.8.8).")
    parser.add_argument("-t", "--timeout", type=float, default=5.0, # Allow float for timeouts
                        help="Timeout in seconds for DNS queries (default: 5.0).")
    parser.add_argument("--http-timeout", type=float, default=10.0, # Allow float
                        help="Timeout in seconds for HTTP requests (crt.sh) (default: 10.0).")
    parser.add_argument("--retries", type=int, default=3, # Reduce default retries slightly for async
                        help="Number of retries for HTTP requests (crt.sh) (default: 3).")
    parser.add_argument("--user-agent", default="AsyncSubdomainDiscovery/1.0",
                        help="User-Agent for HTTP requests.")
    parser.add_argument("--concurrency", type=int, default=100,
                        help="Max concurrent DNS/HTTP tasks (default: 100).") # Add concurrency limit

    args = parser.parse_args()

    if not args.output:
        args.output = f"{args.domain}.txt"
    args.resolvers = [r.strip() for r in args.resolvers.split(',')]

    return args

# --- Async DNS Discovery ---
async def get_subdomains_from_dns(domain, resolvers, timeout, semaphore):
    """
    Asynchronously discovers subdomains via DNS (AXFR, TXT, MX).
    Returns a set of subdomains.
    Uses a semaphore to limit concurrency.
    """
    subdomains = set()
    print(f"[DNS] Using resolvers: {resolvers}")
    async with semaphore: # Limit concurrency for DNS resolver setup/initial queries
        try:
            resolver = dns.asyncresolver.Resolver()
            resolver.nameservers = resolvers
            resolver.timeout = timeout
            resolver.lifetime = timeout

            try:
                print(f"[DNS] Querying NS records for {domain}...")
                ns_records = await resolver.resolve(domain, "NS")
            except dns.resolver.NoNameservers:
                print(f"[DNS] Could not get NS records for {domain}.")
                return set()
            except dns.exception.Timeout:
                 print(f"[DNS] Timeout while getting NS records for {domain}.")
                 return set()
            except Exception as e:
                print(f"[DNS] Error getting NS records: {e}")
                return set()

            ns_servers = [str(ns.target).rstrip('.') for ns in ns_records]
            print(f"[DNS] Found NS Servers: {ns_servers}")

        except Exception as e:
            print(f"[DNS] Error setting up resolver: {e}")
            return set()

    # Attempt AXFR from each NS server concurrently (within semaphore limits)
    axfr_tasks = []
    for ns in ns_servers:
        axfr_tasks.append(attempt_axfr(domain, ns, timeout * 2, semaphore)) # Pass semaphore

    axfr_results = await asyncio.gather(*axfr_tasks, return_exceptions=True)

    axfr_successful = False
    for result in axfr_results:
        if isinstance(result, set) and result:
            subdomains.update(result)
            axfr_successful = True
        elif isinstance(result, Exception):
            # Don't print the full exception here, might be too verbose
            # Let's just note that a task failed. The specific error was printed in attempt_axfr
            print(f"[DNS] Note: An AXFR task failed.")

    # Fallback methods if AXFR failed or yielded no results
    if not subdomains:
        if not axfr_successful:
             print("[DNS] AXFR failed or zone is empty. Trying other DNS methods.")

        async with semaphore: # Limit concurrency for fallback DNS queries
            # Check Wildcard TXT (using the same resolver instance)
            try:
                print("[DNS] Checking for wildcard TXT record...")
                # Use a more random string
                random_prefix = os.urandom(8).hex()
                await resolver.resolve(f"{random_prefix}.{domain}", "TXT")
                print("[DNS] Wildcard detected via TXT.")
            except dns.resolver.NXDOMAIN:
                 print("[DNS] No wildcard TXT detected (NXDOMAIN).")
            except dns.exception.Timeout:
                 print("[DNS] Timeout checking wildcard TXT.")
            except Exception:
                print("[DNS] Could not reliably determine wildcard TXT status.")

            # Check MX records
            try:
                print("[DNS] Checking MX records...")
                mx_records = await resolver.resolve(domain, "MX")
                mx_hosts = set()
                for mx in mx_records:
                    host = str(mx.exchange).rstrip('.').lower()
                    if host.endswith(f".{domain.lower()}"):
                         mx_hosts.add(host)
                if mx_hosts:
                    print(f"[DNS] Found potential subdomains in MX records: {mx_hosts}")
                    subdomains.update(mx_hosts)
                else:
                    print("[DNS] No relevant subdomains found in MX records.")
            except dns.exception.Timeout:
                 print("[DNS] Timeout getting MX records.")
            except Exception as e:
                print(f"[DNS] Could not get MX records: {e}")

    return subdomains

async def attempt_axfr(domain, ns_server, timeout, semaphore):
    """Helper function to attempt AXFR from a single nameserver."""
    async with semaphore: # Acquire semaphore before starting query
        print(f"[DNS] Attempting AXFR from {ns_server}...")
        try:
            loop = asyncio.get_running_loop()
            # Run the synchronous dns.query.xfr in a thread pool executor
            zone_transfer = await loop.run_in_executor(
                None, # Use default executor
                lambda: dns.query.xfr(ns_server, domain, timeout=timeout, relativize=False)
            )
            # Process the zone (synchronously, should be fast)
            zone = dns.zone.from_xfr(zone_transfer)
            found_subs = set()
            count = 0
            for name, node in zone.nodes.items():
                # Construct full name carefully
                full_name = name.to_text(origin=dns.name.Name(domain.split('.') + [''])).rstrip('.')
                # Add only if it's a proper subdomain
                if full_name.endswith(f".{domain}") and full_name != domain:
                     found_subs.add(full_name)
                     count += 1
            if count > 0:
                print(f"[DNS] AXFR successful from {ns_server}, found {count} records.")
                return found_subs
            else:
                 print(f"[DNS] AXFR from {ns_server} successful but zone empty.")
                 return set()
        except dns.exception.FormError:
            print(f"[DNS] AXFR from {ns_server} failed (FormError - likely refused).")
            return set()
        except dns.exception.Timeout:
             print(f"[DNS] AXFR from {ns_server} failed (Timeout).")
             return set()
        except Exception as e:
            print(f"[DNS] AXFR from {ns_server} failed: {e}")
            # Return empty set instead of raising to allow gather to complete
            return set()


# --- Async crt.sh Discovery ---
async def get_subdomains_from_crtsh(domain, session, timeout, max_retries, user_agent, semaphore):
    """
    Asynchronously gets subdomains from crt.sh with improved retry logic.
    Uses a shared aiohttp session and a semaphore.
    Returns a set of subdomains.
    """
    url = f"https://crt.sh/?q={domain}&output=json"
    subdomains = set()
    headers = {'User-Agent': user_agent}
    initial_delay = 2

    print(f"[crt.sh] Querying {url}...")
    async with semaphore: # Limit concurrency for HTTP requests
        for attempt in range(max_retries):
            try:
                # Use a ClientTimeout object for total request timeout
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as response:
                    if response.status == 200:
                        try:
                            text_content = await response.text()
                            if not text_content:
                                print("[crt.sh] Received empty response.")
                                break
                            data = json.loads(text_content)
                            if not data:
                                print("[crt.sh] Received empty JSON data.")
                                break

                            count = 0
                            for entry in data:
                                name_value = entry.get('name_value', '')
                                for name in name_value.splitlines():
                                    clean_name = name.strip().lower()
                                    if clean_name.startswith('*.'):
                                        clean_name = clean_name[2:]
                                    # Ensure it's a subdomain and not the domain itself
                                    if clean_name and clean_name.endswith(f".{domain.lower()}") and clean_name != domain.lower():
                                        if clean_name not in subdomains:
                                             subdomains.add(clean_name)
                                             count += 1
                            print(f"[crt.sh] Successfully parsed response, added {count} new subdomains.")
                            return subdomains # Success

                        except json.JSONDecodeError:
                            # Check if response looks like HTML error page
                            if text_content and ("<html" in text_content.lower() or "<!doctype html" in text_content.lower()):
                                 print(f"[crt.sh] Received HTML instead of JSON (attempt {attempt + 1}/{max_retries}), likely an error page.")
                            else:
                                 print(f"[crt.sh] Error decoding JSON (attempt {attempt + 1}/{max_retries}). Response: {text_content[:200]}...")
                            # Retry might help if it was a temporary server issue returning HTML
                            if attempt >= max_retries - 1: break # But don't retry JSON errors indefinitely

                        except Exception as e:
                             print(f"[crt.sh] Error processing data: {e}")
                             break # Stop if data processing fails

                    # Handle non-200 status codes
                    else:
                        print(f"[crt.sh] Received status code {response.status} (attempt {attempt + 1}/{max_retries}).")
                        if response.status in [500, 502, 503, 504, 429]:
                            if attempt < max_retries - 1:
                                retry_delay = initial_delay * (2 ** attempt)
                                print(f"[crt.sh] Retrying in {retry_delay} seconds...")
                                await asyncio.sleep(retry_delay)
                            else:
                                print("[crt.sh] Max retries reached.")
                                break # Stop after max retries for server errors
                        else:
                            print(f"[crt.sh] Not retrying for status code {response.status}.")
                            break # Don't retry other client errors

            except asyncio.TimeoutError:
                print(f"[crt.sh] Request timed out (attempt {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                     retry_delay = initial_delay * (2 ** attempt)
                     print(f"[crt.sh] Retrying in {retry_delay} seconds...")
                     await asyncio.sleep(retry_delay)
                else:
                     print("[crt.sh] Max retries reached after timeout.")
                     break # Stop after max retries for timeout
            except aiohttp.ClientError as e:
                print(f"[crt.sh] Request failed: {e} (attempt {attempt + 1}/{max_retries}).")
                if attempt < max_retries - 1:
                     retry_delay = initial_delay * (2 ** attempt)
                     print(f"[crt.sh] Retrying in {retry_delay} seconds...")
                     await asyncio.sleep(retry_delay)
                else:
                     print("[crt.sh] Max retries reached after request exception.")
                     break # Stop after max retries for connection errors

    print("[crt.sh] Failed to get data after multiple attempts.")
    return subdomains

# --- Main Async Execution ---
async def main(args):
    print(f"[*] Starting async subdomain discovery for: {args.domain}")
    print(f"[*] Output file: {args.output}")
    print(f"[*] Concurrency limit: {args.concurrency}")

    # Create a semaphore to limit concurrent tasks
    semaphore = asyncio.Semaphore(args.concurrency)

    # Create a shared aiohttp session
    # Configure connector to potentially avoid aiodns if issues persist, though the policy fix should work
    # connector = aiohttp.TCPConnector(resolver=aiohttp.AsyncResolver()) # Use default async resolver
    # connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()) # Alternative if needed
    async with aiohttp.ClientSession() as session: # Use default connector for now
        # Prepare tasks
        dns_task = get_subdomains_from_dns(args.domain, args.resolvers, args.timeout, semaphore)
        crtsh_task = get_subdomains_from_crtsh(args.domain, session, args.http_timeout, args.retries, args.user_agent, semaphore)

        # Run tasks concurrently
        results = await asyncio.gather(dns_task, crtsh_task, return_exceptions=True)

    # Process results
    dns_subs = set()
    crtsh_subs = set()

    if isinstance(results[0], set):
        dns_subs = results[0]
        print(f"\n[*] [DNS] Found {len(dns_subs)} subdomains.")
    elif isinstance(results[0], Exception):
        print(f"\n[!] [DNS] Task failed: {results[0]}")

    if isinstance(results[1], set):
        crtsh_subs = results[1]
        print(f"\n[*] [crt.sh] Found {len(crtsh_subs)} subdomains.")
    elif isinstance(results[1], Exception):
        print(f"\n[!] [crt.sh] Task failed: {results[1]}")


    # Combine and sort results
    all_subdomains = sorted(dns_subs.union(crtsh_subs))
    print(f"\n[*] Total unique subdomains found: {len(all_subdomains)}")

    # Save results to file (synchronous file I/O is usually fine)
    try:
        output_path = os.path.abspath(args.output)
        output_dir = os.path.dirname(output_path)
        if output_dir:
             os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            if not all_subdomains:
                 f.write("# No subdomains found.\n")
            for sub in all_subdomains:
                f.write(f"{sub}\n")
        print(f"\n[*] Results saved to: {output_path}")
    except Exception as e:
        print(f"[!] Error writing to file {output_path}: {e}")

    print("[*] Discovery finished.")


if __name__ == '__main__':
    # Fix for aiodns/aiohttp on Windows: use SelectorEventLoop
    if platform.system() == "Windows":
        print("[*] Applying WindowsSelectorEventLoopPolicy for asyncio compatibility.")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    args = parse_arguments()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n[*] Execution interrupted by user.")
        sys.exit(0)
    # Catch the specific RuntimeError we saw
    except RuntimeError as e:
        print(f"\n[!] Runtime Error: {e}")
        if "Event loop is closed" in str(e):
             print("[!] This might indicate an issue with asyncio cleanup or resource handling.")
        elif "SelectorEventLoop" in str(e):
             print("[!] This might be related to asyncio event loop compatibility on Windows.")
             print("[!] Ensure you have the latest versions of aiohttp and aiodns, or try installing 'winloop'.")
        sys.exit(1)
    except Exception as e: # Catch any other unexpected errors
        print(f"\n[!] An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
