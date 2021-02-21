import io
import os
import sys
import time
import threading
import requests
import re
import hashlib
import typing
import queue


from cdn_downloader_v5 import downloader
from cdn_downloader_v5 import download_progress

# 最后一次代码修改时间
__updated__ = "2021-02-21 13:03:59"
__version__ = 0.1

if __name__ == "__main__":
    specific_ip_address = "1.0.0.66"
    # specific_ip_address = "1.0.0.100"
    # specific_ip_address = None
    sha256_hash_value = None
    # url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    # sha256_hash_value = "6182BB277CE268F10BCA7DB3A16B9475F75B7D861907C7EFB188A01420C5B780"
    url = "https://speed.haoren.ml/cache.jpg"
    # sha256_hash_value = "A0D7DD06B54AFBDFB6811718337C6EB857885C489DA6304DAB1344ECC992B3DB"
    # 128 MiB version
    sha256_hash_value = "45A3AE1D8321E9C99A5AEEA31A2993CF1E384661326C3D238FFAFA2D7451AEDB"
    # url = "https://speed.cloudflare.com/__down?bytes=90"
    # sha256_hash_value = None
    # url = "http://127.0.0.1/download/text/123.txt"
    # sha256_hash_value = "3DCCBFEE56F49916C3264C6799174AF2FDDDEE75DD98C9E7EA5DF56C6874F0D7"
    down = downloader(
        url=url, 
        specific_ip_address=specific_ip_address, 
        specific_range=(0,134217728) )

    down.speedtest_single_thread(timeout_to_stop=10)
    dp:download_progress = down.download_progress_list[0]
    print("downloaded size:",down.get_humanize_size(dp.curr_getsize))

