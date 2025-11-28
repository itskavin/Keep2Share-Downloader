import time
import requests
from io import BytesIO
from random import choice
from PIL import Image
from typing import List, Dict, Any, Optional

DOMAINS = ["k2s.cc"]

class K2SError(Exception):
    pass

class FileNotFoundError(K2SError):
    pass

class K2SAPI:
    def __init__(self, proxy_manager=None):
        self.proxy_manager = proxy_manager

    def _get_proxy_dict(self, proxy: str = None) -> Optional[Dict[str, str]]:
        if proxy:
            return {'https': f'http://{proxy}'}
        return None

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        try:
            r = requests.post(f"https://{choice(DOMAINS)}/api/v2/getFilesInfo", json={
                "ids": [file_id]
            }).json()
            # print(f"DEBUG: getFilesInfo response: {r}")
            if r.get('status') == 'success' and r.get('files'):
                file_info = r['files'][0]
                if file_info.get('is_available') is False:
                     raise FileNotFoundError(f"File {file_id} is not available (deleted or private).")
                return file_info
            raise K2SError(f"Failed to get file info: {r}")
        except Exception as e:
            raise K2SError(f"Network error getting file info: {e}")

    def get_name(self, file_id: str) -> str:
        info = self.get_file_info(file_id)
        return info.get('name') or 'unknown_file'

    def request_captcha(self) -> Dict[str, Any]:
        try:
            return requests.post(f"https://{choice(DOMAINS)}/api/v2/requestCaptcha").json()
        except Exception as e:
            raise K2SError(f"Failed to request captcha: {e}")

    def solve_captcha(self, captcha_url: str) -> str:
        print("Downloading captcha...")
        r = requests.get(captcha_url)
        im = Image.open(BytesIO(r.content))
        
        # Try to show image, fallback to saving it
        try:
            im.show()
        except:
            im.save("captcha.png")
            print("Could not open image viewer. Captcha saved to captcha.png")
            
        return input("Enter captcha response: ")

    def generate_download_urls(self, file_id: str, count: int = 1) -> List[str]:
        proxies = self.proxy_manager.get_all() if self.proxy_manager else [None]
        
        # Initial captcha
        captcha_data = self.request_captcha()
        captcha_response = self.solve_captcha(captcha_data["captcha_url"])
        
        working_link = False
        free_download_key = None
        working_proxy = None
        
        # Try to get a working link/key
        for proxy in proxies:
            print(f"Trying proxy: {proxy if proxy else 'Direct'}")
            prox_dict = self._get_proxy_dict(proxy)
            
            try:
                payload = {
                    "file_id": file_id,
                    "captcha_challenge": captcha_data["challenge"],
                    "captcha_response": captcha_response
                }
                
                resp = requests.post(
                    f"https://{choice(DOMAINS)}/api/v2/getUrl", 
                    json=payload, 
                    proxies=prox_dict, 
                    timeout=10
                ).json()
                
                if resp.get('status') == 'error':
                    msg = resp.get('message', '')
                    if msg == "Invalid captcha code":
                        print("Invalid captcha. Retrying...")
                        captcha_data = self.request_captcha()
                        captcha_response = self.solve_captcha(captcha_data["captcha_url"])
                        continue # Retry with same proxy or next? Let's retry same logic
                    elif msg == "File not found":
                        raise FileNotFoundError("The file was not found on K2S.")
                    else:
                        print(f"API Error: {msg}")
                        continue

                if "time_wait" in resp:
                    wait_time = resp['time_wait']
                    if wait_time > 60: # Skip if wait is too long
                        print(f"Wait time too long ({wait_time}s). Skipping proxy.")
                        continue
                        
                    print(f"Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    
                    free_download_key = resp.get('free_download_key')
                    working_link = True
                    working_proxy = proxy
                    break
                else:
                    # No wait time, maybe direct URL or key?
                    if 'url' in resp:
                        return [resp['url']] # Direct download
                    if 'free_download_key' in resp:
                        free_download_key = resp['free_download_key']
                        working_link = True
                        working_proxy = proxy
                        break
                        
            except FileNotFoundError:
                raise
            except Exception as e:
                print(f"Error with proxy {proxy}: {e}")
                continue
        
        if not working_link or not free_download_key:
            raise K2SError("Could not generate a download key with available proxies.")

        # Now generate the actual URLs using the key
        urls = []
        print(f"Generating {count} download links using proxy {working_proxy}...")
        
        for _ in range(count):
            try:
                r = requests.post(f"https://{choice(DOMAINS)}/api/v2/getUrl", json={
                    "file_id": file_id,
                    "free_download_key": free_download_key
                }, proxies=self._get_proxy_dict(working_proxy)).json()
                
                if r.get('url'):
                    urls.append(r['url'])
                else:
                    print(f"Failed to generate link: {r}")
            except Exception as e:
                print(f"Error generating link: {e}")
                pass
                
        return urls
