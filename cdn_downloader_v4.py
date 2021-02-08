from concurrent.futures import ThreadPoolExecutor
import requests
import time
import threading
import re
from urllib import parse
import hashlib

from requests.sessions import HTTPAdapter
from forced_ip_https_adapter import ForcedIPHTTPSAdapter

# 源自：https://blog.csdn.net/qq_42951560/article/details/108785802

__updated__= "2021-02-08 16:56:57"

status_init = 0
status_ready = 1
status_running = 2
status_work_finished = 3
status_exit = 4
status_force_exit = 5

class download_progress:
    def __init__(self,
        start, end, my_thread_id=0, **kwargs):
        # worker 最开始分配任务的起始和终点
        self.init_start = start
        self.init_end = end
        # start,end,getsize 仅仅表示当前任务状态
        # start: 起始下载位置，end: 终点下载位置
        # getsize: 当前任务的累计下载大小
        self.curr_start = start
        self.curr_end = end
        self.curr_getsize = 0
        # 通过 hack 手段强行终止当前任务所需要的context
        self.request_context = None
        # 控制一个 worker 持续循环接收数据的开关
        self.keep_run = True
        # 是否不断坚持发起请求
        self.keep_get_new_request = True
        # 一个worker固有属性
        self.my_thread_id = my_thread_id
        # 统计这个worker重新发起请求的次数
        self.retry_count = 0
        # watchdog看门狗通过hack手段终结request的次数
        self.hack_send_close_signal_count = 0
        # 给watchdog看门狗看家的必要数据
        self.history_getsize = 0
        # 千真万确的实际累计下载大小
        self.history_done_size = 0
        # 这个worker目前的工作状态
        self.downloader_thread_status = status_init

    def now_init(self):
        self.downloader_thread_status = status_init
    
    def now_ready(self):
        self.downloader_thread_status = status_ready

    def now_running(self):
        self.downloader_thread_status = status_running

    def now_work_finished(self):
        self.downloader_thread_status = status_work_finished

    def now_exit(self):
        self.downloader_thread_status = status_exit

    def now_force_exit(self):
        self.downloader_thread_status = status_force_exit

class downloader:
    const_one_of_1024 = 0.0009765625 # 1/1024
    def __init__(self, url:str, name:str, **kwargs):
        self.url = url
        self.name = name        
        # 看门狗检查线程的频率，每多少秒检查一次
        self.watchdog_frequent = 5
        # 设置超时时间，超出后立即重试
        self.timeout = 3
        self.each_retries = 3
        self.stream = True
        self.sni_verify = True
        self.thread_num = 4
        self.sha256_hash_value = None

        if "max_retries" in kwargs:
            self.each_retries = int(kwargs.pop("max_retries"))
        if "timeout" in kwargs:
            self.timeout = float(kwargs.pop("timeout"))
        if "stream" in kwargs:
            self.stream = bool(kwargs.pop("stream"))
        if "verify" in kwargs:
            self.sni_verify = bool(kwargs.pop("verify"))
        if "thread_num" in kwargs:
            self.thread_num = int(kwargs.pop("thread_num"))
        if "sha256_hash_value" in kwargs:
            self.sha256_hash_value = str(kwargs.pop("sha256_hash_value")).upper()

        self.kwargs = kwargs

        self.download_tp = None
        self.download_progress_list = []
        self.futures = []
        
        self.url_parse = parse.urlparse(url=url)
        self.hostname = self.url_parse.hostname
        self.is_https = False
        self.specific_ip_address = None
        self.ip_direct_url = None
        
        if self.url_parse.scheme == "https":
            self.is_https = True
        
        if "specific_ip_address" in self.kwargs:
            self.specific_ip_address = str(kwargs.pop("specific_ip_address"))
            if not self.is_https:
                pattern = re.compile(r"http://"+self.hostname)
                self.ip_direct_url = re.sub(pattern, \
                    repl="http://"+self.specific_ip_address ,string=self.url)
        # 发起URL请求，将response对象存入变量 r
        session = requests.Session()
        if self.is_https:
            if self.specific_ip_address == None :
                session.mount(prefix="https://", adapter=ForcedIPHTTPSAdapter(max_retries=self.each_retries) )
            else:
                session.mount(prefix="https://" , adapter=ForcedIPHTTPSAdapter(max_retries=self.each_retries, 
                    dest_ip=self.specific_ip_address))
        else:
            session.mount(prefix="http://", adapter=HTTPAdapter(max_retries=self.each_retries) )
        r = session.head( url=self.url, allow_redirects=True, verify=self.sni_verify)
        # 从回复数据获取文件大小
        self.size = int(r.headers["Content-Length"])

    def chunk_download_retry(self,dp:download_progress):
        dp.now_init()
        part_of_done_size = int(dp.curr_getsize * 0.8)
        dp.history_done_size += part_of_done_size
        dp.curr_start = dp.curr_start + part_of_done_size
        dp.retry_count += 1
        dp.curr_getsize = 0
        dp.history_getsize = 0
        self.get_new_request(dp=dp)

    def get_new_request(self, dp:download_progress):
        dp.now_init()
        if (dp.request_context != None):
            dp.request_context.close()
            dp.request_context = None
        headers = {
            "range": f"bytes={dp.curr_start}-{dp.curr_end}", 
            "host": self.hostname, 
            "User-Agent": "python 3.8.5/requests 2.24.0 (github.com@xfl12345;cloudflare-better-node v0.4)" }
        session = requests.Session()
        my_request = None
        retry_count = 0
        while dp.keep_get_new_request:
            try:
                if self.is_https:
                    if self.specific_ip_address == None :
                        session.mount(prefix="https://", adapter=ForcedIPHTTPSAdapter(max_retries=self.each_retries) )
                    else:
                        session.mount(prefix="https://" , adapter=ForcedIPHTTPSAdapter(max_retries=self.each_retries, 
                            dest_ip=self.specific_ip_address))
                    my_request = session.get(url=self.url, headers=headers, 
                        stream=self.stream, timeout=self.timeout, verify=self.sni_verify)
                else:
                    session.mount(prefix="http://", adapter=HTTPAdapter(max_retries=self.each_retries) )
                    my_request = session.get(url=self.ip_direct_url, headers=headers, 
                        stream=self.stream, timeout=self.timeout)
            except (requests.Timeout, requests.ReadTimeout ) :
                retry_count = retry_count +1
                print(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"request time out.Retry count={retry_count * self.each_retries}")
            except Exception as e:
                retry_count = retry_count +1
                print(f"request:my_thread_id={dp.my_thread_id}," + \
                    "unknow error.Retrying...error=",e)
            else:
                dp.request_context = my_request
                break
        dp.now_ready()

    # 默认每次拉取 10KiB 大小的数据块
    def download(self, dp:download_progress, **kwargs):
        self.get_new_request(dp=dp)
        iter_content_param = []
        start_time = time.time()
        with open(self.name, "rb+") as f:
            f.seek(dp.curr_start)
            if "chunk_size" in kwargs:
                iter_content_param.append( int(kwargs.pop("chunk_size")) )
            it = dp.request_context.iter_content( *iter_content_param )
            dp.now_running()
            while dp.keep_run:
                try:
                    chunk_data = next(it)
                    f.write(chunk_data)
                    # 统计已下载的数据大小，单位是字节（byte）
                    dp.curr_getsize += len(chunk_data)
                except StopIteration:
                    it.close()
                    break
                except requests.ConnectionError as e:
                    print(f"worker:my_thread_id={dp.my_thread_id},error=",e)
                    if(dp.curr_start + dp.curr_getsize <  dp.curr_end):
                        print(f"worker:my_thread_id={dp.my_thread_id},"+\
                            "did not finished yet.Retrying...")
                        self.chunk_download_retry(dp=dp)
                        it = dp.request_context.iter_content( *iter_content_param )
                        f.seek(dp.curr_start)
                        dp.now_running()
        if(dp.curr_start + dp.curr_getsize <  dp.curr_end):
            print(f"worker:my_thread_id={dp.my_thread_id}," + \
                f"start={dp.curr_start} + getsize={dp.curr_getsize} < end={dp.curr_end}," + \
                "exit abnormally.")
            dp.now_force_exit()
            return None
        try:
            dp.request_context.close()
        except Exception:
            pass
        end_time = time.time()
        dp.now_work_finished()
        tmp_curr_getsize = dp.curr_getsize
        dp.curr_getsize = 0
        dp.history_done_size += tmp_curr_getsize
        total_time = end_time - start_time
        total_size = dp.history_done_size
        average_speed = self.get_humanize_size(size_in_byte = total_size/total_time )
        print(f"worker:my_thread_id={dp.my_thread_id},my job had done." +\
             f"Total downloaded:{self.get_humanize_size(total_size)}," +\
             f"total_time={(total_time):.3f}s,"+ \
             f"average_speed: {average_speed}/s,"+ \
             f"retry_count={dp.retry_count}")
        dp.now_exit()

    # 自动转化字节数为带计算机1024进制单位的字符串
    def get_humanize_size(self, size_in_byte):
        size_in_byte = int(size_in_byte)
        if size_in_byte < 1024: # size under 1024 bytes (1KiB)
            return str(size_in_byte) + "byte"
        elif size_in_byte < 0x100000: # size under 1MiB (1048576 Bytes)
            result_num = (size_in_byte >> 10) + \
                ((size_in_byte & 0x3FF)*self.const_one_of_1024 )
            return ("%.3f"%result_num) + "KiB"
        elif size_in_byte < 0x40000000: # size under 1GiB (1073741824 Bytes)
            result_num = (size_in_byte >> 20) + \
                (((size_in_byte & 0xFFC00) >> 10)*self.const_one_of_1024 )
            return ("%.3f"%result_num) + "MiB"
        # size equal or greater than 1GiB... Wow!
        result_num = (size_in_byte >> 30) + \
                (((size_in_byte & 0x3FF00000) >> 20)*self.const_one_of_1024 )
        return ("%.3f"%result_num) + "GiB"
    
    def dp_list_getsize(self):
        for dp in self.download_progress_list:
            yield (dp.history_done_size + dp.curr_getsize)
    
    def dp_list_truesize(self):
        for dp in self.download_progress_list:
            yield dp.history_done_size

    def download_monitor_str(self):
        while True:
            last = sum( self.dp_list_getsize() )
            time.sleep(1)
            curr = sum( self.dp_list_getsize() )
            complete_size = curr
            process = complete_size / self.size * 100
            complete_size = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            print(f"downloaded: {complete_size:10} | process: {process:6.2f}% | speed: {speed}/s {' '*5}", end="\r")
            if process >= 100 and \
                sum(self.dp_list_truesize()) == self.size:
                print(f"downloaded: {complete_size:10} | process: {100.00:6}% | speed:  0Byte/s ", end=" | ")
                break

    def download_watchdog(self):
        print("download_watchdog is running...")
        dp_list = self.download_progress_list
        # def oh_it_is_ok():
        #     print(f"watchdog:thread_id={curr_dp.my_thread_id}," + \
        #         "everything looks fine.No need restart.")

        while(sum( self.dp_list_truesize() ) < self.size):
            time.sleep(self.watchdog_frequent)
            for i in range(self.thread_num):
                # curr_dp = download_progress(dp_list[i]) 
                curr_dp = dp_list[i]
                ds = curr_dp.downloader_thread_status
                if ds == status_running :
                    if curr_dp.curr_getsize - curr_dp.history_getsize > 0 :
                        curr_dp.history_getsize = curr_dp.curr_getsize
                    else:
                        if curr_dp.keep_run == True:
                            print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                                f"had blocked over {self.watchdog_frequent} seconds!"+\
                                f"retry_count={curr_dp.retry_count},Restarting...")
                            curr_dp.keep_run = False
                        else:
                            print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                                f"failed to terminate!Retrying...{' '*30}")
                        curr_dp.request_context.raw._fp.close()
                elif ds == status_force_exit:
                    self.chunk_download_retry(dp=curr_dp)
                    tmp_total_size = curr_dp.curr_end - curr_dp.curr_start
                    print("watchdog:Submit a worker,"+\
                        f"my_thread_id={curr_dp.my_thread_id},"+\
                        f"start_from={curr_dp.curr_start}," + \
                        f"end_at={curr_dp.curr_end},"+\
                        f"total_work_load={self.get_humanize_size(tmp_total_size)}.")
                    try:
                        future = self.download_tp.submit(self.download,
                            dp=curr_dp, chunk_size=1024 )
                        # dp_list[i] = curr_dp
                        self.futures[i] = future
                    except Exception as e:
                        print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                            f"restart failed!Error=",e)
                    else:
                        print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                            f"restart succeed!{' '*30}")

    def main(self):
        f = open(self.name, "wb")
        # 优先创建 size 大小的占位文件
        f.truncate(self.size)
        f.close()
        self.download_tp = ThreadPoolExecutor(max_workers=self.thread_num)
        start = 0
        part_size = int(self.size / self.thread_num)
        start_time = time.time()
        for i in range(self.thread_num):
            if(i+1 == self.thread_num):
                end = self.size
            else:
                end = (i+1) * part_size
            dp = download_progress(start=start, end=end, my_thread_id=i)
            self.download_progress_list.append(dp)
            future = self.download_tp.submit(self.download, dp=dp, chunk_size=256 )
            # future = tp.submit(self.download, start, end, my_thread_id=i )
            print(f"Submit a worker,my_thread_id={i},start_from={start}," + \
                f"end_at={end},total_work_load={self.get_humanize_size(end-start)}")
            self.futures.append(future)
            start = end+1
        
        # TODO: 把下载监视器写到多线程任务submit之前，要求用独立线程，自退出
        dms_thread = threading.Thread(target=self.download_monitor_str, daemon=True)
        dms_thread.start()
        dw_thread = threading.Thread(target=self.download_watchdog, daemon=True)
        dw_thread.start()
        # print("keep running")
        dms_thread.join()
        # self.download_monitor_str()

        self.download_tp.shutdown()
        end_time = time.time()
        total_time = end_time - start_time
        average_speed = self.get_humanize_size(size_in_byte = self.size/total_time )
        print(f"total-time: {total_time:.3f}s | average-speed: {average_speed}/s")
        if self.sha256_hash_value != None:
            print("Given sha256 hash value is   :" + self.sha256_hash_value)
            with open(self.name, "rb") as f:
                sha256_obj = hashlib.sha256()
                sha256_obj.update(f.read())
                hash_value = sha256_obj.hexdigest().upper()
                print("Compute sha256 hash value is :" + hash_value)
                if (hash_value == self.sha256_hash_value):
                    print("Hash matched!")
                else:
                    print("Hash not match.Maybe file is broken.")


if __name__ == "__main__":
    sha256_hash_value = "A0D7DD06B54AFBDFB6811718337C6EB857885C489DA6304DAB1344ECC992B3DB"
    # url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    url = "https://speed.haoren.ml/cache.jpg"
    # specific_ip_address = "1.0.0.66"
    # url = "https://speed.cloudflare.com/__down?bytes=10000000000"
    down = downloader(url=url, name="cache.jpg", \
        specific_ip_address="1.0.0.0", thread_num=32, 
        sha256_hash_value = sha256_hash_value )
    down.main()
    

    
