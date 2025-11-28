import re
import os
import sys
import random
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

class ProxyManager:
    def __init__(self, proxy_file: str = "proxies.txt"):
        self.proxy_file = proxy_file
        self.proxies = self._load_proxies()
        self.lock = threading.Lock()

    def _load_proxies(self) -> List[str]:
        if os.path.exists(self.proxy_file):
            with open(self.proxy_file, "r") as f:
                return [p.strip() for p in f.read().splitlines() if p.strip()]
        return []

    def fetch_proxies(self):
        print("Fetching proxies...")
        proxies = set()
        try:
            r = requests.get("https://api.proxyscrape.com/?request=getproxies&proxytype=https&timeout=10000&country=all&ssl=all&anonymity=all")
            proxies.update(r.text.splitlines())
            r = requests.get("https://api.proxyscrape.com/?request=getproxies&proxytype=http&timeout=10000&country=all&ssl=all&anonymity=all")
            proxies.update(r.text.splitlines())
        except Exception as e:
            print(f"Error fetching proxies: {e}")
        
        print(f"Found {len(proxies)} raw proxies. Validating...")
        self.proxies = self._validate_proxies(list(proxies))
        self._save_proxies()

    def _validate_proxies(self, proxies: List[str]) -> List[str]:
        working_proxies = []
        random.shuffle(proxies) # Shuffle to get a random sample if we stop early
        
        print(f"Validating {len(proxies)} proxies (stopping after 50 working ones)...")
        
        with ThreadPoolExecutor(max_workers=200) as executor:
            future_to_proxy = {executor.submit(self._check_proxy, proxy): proxy for proxy in proxies}
            
            for future in as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                try:
                    if future.result():
                        working_proxies.append(proxy)
                        print(f"Found working proxy: {proxy} ({len(working_proxies)}/50)", end='\r')
                        if len(working_proxies) >= 50:
                            print("\nFound enough proxies. Stopping validation.")
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                except:
                    pass
                    
        return working_proxies

    def _check_proxy(self, proxy: str) -> bool:
        try:
            requests.get('https://api.myip.com', proxies={'https': f'http://{proxy}'}, timeout=3)
            return True
        except:
            return False

    def _save_proxies(self):
        with open(self.proxy_file, "w") as f:
            f.write("\n".join(self.proxies))

    def get_proxy(self) -> Optional[str]:
        with self.lock:
            if not self.proxies:
                return None
            return self.proxies[0] 

    def get_all(self) -> List[str]:
        return self.proxies

def parse_size(size: str) -> int:
    units = {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30, "TB": 2**40 ,
             "":  1, "KIB": 10**3, "MIB": 10**6, "GIB": 10**9, "TIB": 10**12}
    m = re.match(r'^([\d\.]+)\s*([a-zA-Z]{0,3})$', str(size).strip())
    if not m:
        return 0
    number, unit = float(m.group(1)), m.group(2).upper()
    return int(number * units.get(unit, 1))

def human_readable_bytes(num: int) -> str:
    for x in ['bytes', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024.0:
            return "%3.3f %s" % (num, x)
        num /= 1024.0
    return "%3.3f %s" % (num, 'TB')
