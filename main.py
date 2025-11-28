import os
import re
import sys
import time
import math
import argparse
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import List, Dict

import k2s
from utils import ProxyManager, parse_size, human_readable_bytes

class Downloader:
    def __init__(self, url: str, filename: str = None, split_size: int = 20 * 1024 * 1024, threads: int = 5, use_proxies: bool = True, refresh_proxies: bool = False):
        self.url = url
        self.filename = filename
        self.split_size = split_size
        self.threads = threads
        self.use_proxies = use_proxies
        self.refresh_proxies = refresh_proxies
        
        self.proxy_manager = ProxyManager() if use_proxies else None
        self.k2s_api = k2s.K2SAPI(self.proxy_manager)
        self.file_id = self._extract_file_id(url)
        
        if not self.file_id:
            raise ValueError("Invalid K2S URL")

    def _extract_file_id(self, url: str) -> str:
        m = re.search(r"https:\/\/(k2s.cc|keep2share.cc)\/file\/([a-zA-Z0-9]+)", url)
        return m.group(2) if m else None

    def start(self):
        print(f"Initializing download for ID: {self.file_id}")
        
        # 1. Get File Info
        try:
            if not self.filename:
                self.filename = self.k2s_api.get_name(self.file_id)
            print(f"Target Filename: {self.filename}")
        except k2s.FileNotFoundError:
            print("Error: File not found on Keep2Share.")
            return
        except Exception as e:
            print(f"Error getting file info: {e}")
            return

        # 2. Prepare Proxies
        if self.use_proxies and self.proxy_manager:
            if self.refresh_proxies or not self.proxy_manager.get_all():
                print("Fetching new proxies...")
                self.proxy_manager.fetch_proxies()
            else:
                print(f"Using {len(self.proxy_manager.get_all())} existing proxies.")
        else:
            print("Proxies disabled. Using direct connection.")
        
        # 3. Generate Download Links
        try:
            # We need enough links for threads? Or just one valid link?
            # K2S free links might expire or be IP bound.
            # Let's try to get a few links.
            download_urls = self.k2s_api.generate_download_urls(self.file_id, count=self.threads)
        except k2s.FileNotFoundError:
            print("Error: File not found during link generation.")
            return
        except Exception as e:
            print(f"Error generating links: {e}")
            return

        if not download_urls:
            print("No download URLs generated.")
            return

        # 4. Get File Size from one of the links
        try:
            head = requests.head(download_urls[0], allow_redirects=True)
            total_size = int(head.headers.get('content-length', 0))
        except:
            print("Could not determine file size.")
            return

        print(f"File Size: {human_readable_bytes(total_size)}")
        
        # 5. Prepare Chunks
        num_splits = math.ceil(total_size / self.split_size)
        print(f"Splitting into {num_splits} chunks of {human_readable_bytes(self.split_size)}")
        
        pathlib_tmp = os.path.join("tmp")
        os.makedirs(pathlib_tmp, exist_ok=True)
        
        chunks = []
        for i in range(num_splits):
            start = i * self.split_size
            end = min((i + 1) * self.split_size - 1, total_size - 1)
            chunks.append({
                'id': i,
                'start': start,
                'end': end,
                'size': end - start + 1,
                'filename': os.path.join(pathlib_tmp, f"{self.filename}.part{str(i).zfill(3)}")
            })

        # 6. Download Loop
        pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc=self.filename)
        
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            futures = []
            for chunk in chunks:
                # Check if already downloaded
                if os.path.exists(chunk['filename']):
                    if os.path.getsize(chunk['filename']) == chunk['size']:
                        pbar.update(chunk['size'])
                        continue
                    else:
                        os.remove(chunk['filename'])
                
                # Assign a URL to this chunk (round robin)
                url = download_urls[chunk['id'] % len(download_urls)]
                futures.append(executor.submit(self._download_chunk, url, chunk, pbar))
            
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Chunk download failed: {e}")
                    # Retry logic could go here
        
        pbar.close()
        
        # 7. Assemble
        self._assemble_file(chunks)

    def _download_chunk(self, url: str, chunk: Dict, pbar: tqdm):
        headers = {'Range': f"bytes={chunk['start']}-{chunk['end']}"}
        max_retries = 5
        for attempt in range(max_retries):
            try:
                r = requests.get(url, headers=headers, stream=True, timeout=30)
                r.raise_for_status()
                with open(chunk['filename'], 'wb') as f:
                    for data in r.iter_content(chunk_size=8192):
                        f.write(data)
                        pbar.update(len(data))
                return
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [429, 509, 503]:
                    wait_time = (attempt + 1) * 5
                    # print(f"Rate limited (chunk {chunk['id']}). Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                raise Exception(f"HTTP Error chunk {chunk['id']}: {e}")
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to download chunk {chunk['id']}: {e}")
                time.sleep(2)

    def _assemble_file(self, chunks: List[Dict]):
        print("Assembling file...")
        if os.path.exists(self.filename):
            os.remove(self.filename)
            
        with open(self.filename, 'wb') as outfile:
            for chunk in chunks:
                if os.path.exists(chunk['filename']):
                    with open(chunk['filename'], 'rb') as infile:
                        outfile.write(infile.read())
                    os.remove(chunk['filename'])
                else:
                    print(f"Missing chunk {chunk['id']}!")
                    return
        
        print(f"Download complete: {self.filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Clean K2S Downloader')
    parser.add_argument('url', help='K2S URL')
    parser.add_argument('--filename', help='Output filename')
    parser.add_argument('--split-size', default='20MB', help='Chunk size (e.g. 10MB)')
    parser.add_argument('--threads', type=int, default=1, help='Number of threads (Default: 1 for free downloads)')
    parser.add_argument('--no-proxy', action='store_true', help='Disable proxies and use direct connection')
    parser.add_argument('--refresh', action='store_true', help='Refresh proxy list')
    
    args = parser.parse_args()
    
    try:
        downloader = Downloader(
            url=args.url,
            filename=args.filename,
            split_size=parse_size(args.split_size),
            threads=args.threads,
            use_proxies=not args.no_proxy,
            refresh_proxies=args.refresh
        )
        downloader.start()
    except Exception as e:
        print(f"Error: {e}")
