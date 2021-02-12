from io import IOBase
import io
import os
import time
import threading
import requests
import re
import hashlib

from concurrent.futures import ThreadPoolExecutor
from urllib import parse
from requests.models import Response
from requests.sessions import HTTPAdapter, default_headers
from forced_ip_https_adapter import ForcedIPHTTPSAdapter

# 源自：https://blog.csdn.net/qq_42951560/article/details/108785802

__updated__= "2021-02-12 22:55:45"

status_init = 0
status_ready = 1
status_running = 2
status_work_finished = 3
status_exit = 4
status_force_exit = 5
level_enforce = 0
level_permissive = 1

class download_progress:
    def __init__(self,
        start, 
        end, 
        my_thread_id:int=0, 
        chunk_size:int=512,
        getsize_strict_level=level_enforce,
        **kwargs):
        self.chunk_size = chunk_size
        self.getsize_strict_level = getsize_strict_level
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
        # TODO: 实现暂停功能
        # 使 worker 陷入暂停
        # self.pause = False
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

    def get_curr_workload(self):
        return self.curr_end - self.curr_start +1

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
    user_agent = "python 3.8.5/requests 2.24.0 (github.com@xfl12345;cloudflare-better-node v0.5)"
    default_filename = "url_did_not_provide_filename"
    def __init__(self, 
            url:str, 
            filename:str="url_did_not_provide_filename", 
            storage_root:str="downloads/",
            thread_num:int=4,
            max_retries:int=3,
            timeout:float=3,
            stream:bool=True,
            sni_verify:bool=True,
            sha256_hash_value:str=None,
            specific_ip_address:str=None,
            **kwargs):
        self.url = url
        self.filename = filename 
        self.storage_root = storage_root
        self.full_path_to_file = storage_root + filename
        # 看门狗检查线程的频率，每多少秒检查一次
        self.watchdog_frequent = 5
        # 设置超时时间，超出后立即重试
        self.timeout = timeout
        self.max_retries = max_retries
        self.stream = stream
        self.sni_verify = sni_verify
        self.thread_num = thread_num
        self.sha256_hash_value = None
        self.specific_ip_address = specific_ip_address

        if (sha256_hash_value != None):
            self.sha256_hash_value = sha256_hash_value.upper()
        if not os.path.exists(self.storage_root):
            os.makedirs(self.storage_root)

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
        
        if self.specific_ip_address != None and not self.is_https:
            pattern = re.compile(r"http://"+self.hostname)
            self.ip_direct_url = re.sub(pattern, \
                repl="http://"+self.specific_ip_address ,string=self.url)

    #source code URL:https://blog.csdn.net/mbh12333/article/details/103721834
    def get_file_name(self,url:str, response:Response):
        filename = ''
        headers = response.headers
        if 'Content-Disposition' in headers and headers['Content-Disposition']:
            disposition_split = headers['Content-Disposition'].split(';')
            if len(disposition_split) > 1:
                if disposition_split[1].strip().lower().startswith('filename='):
                    file_name = disposition_split[1].split('=')
                    if len(file_name) > 1:
                        filename = parse.unquote(file_name[1])
        if not filename and os.path.basename(url):
            filename = os.path.basename(url).split("?")[0]
        if not filename:
            return time.time()
        return filename

    def chunk_download_retry(self,dp:download_progress):
        dp.now_init()
        part_of_done_size = int(dp.curr_getsize * 0.8)
        dp.history_done_size += part_of_done_size
        dp.curr_start = dp.curr_start + part_of_done_size
        dp.retry_count += 1
        dp.curr_getsize = 0
        dp.history_getsize = 0
        self.get_new_request(dp=dp)

    def get_session_obj(self)->requests.Session:
        session = requests.Session()
        if self.is_https:
            if self.specific_ip_address == None :
                session.mount(prefix="https://", adapter=ForcedIPHTTPSAdapter(max_retries=self.max_retries) )
            else:
                session.mount(prefix="https://" , adapter=ForcedIPHTTPSAdapter(max_retries=self.max_retries, 
                    dest_ip=self.specific_ip_address))
        else:
            session.mount(prefix="http://", adapter=HTTPAdapter(max_retries=self.max_retries) )
        return session

    def get_new_request(self, dp:download_progress, need_return:bool=False)->Response:
        dp.now_init()
        if (dp.request_context != None):
            dp.request_context.close()
            dp.request_context = None
        headers = {
            "Range": f"bytes={dp.curr_start}-{dp.curr_end}", 
            "Host": self.hostname, 
            "User-Agent": self.user_agent }
        session = self.get_session_obj()
        my_request = None
        retry_count = 0
        while dp.keep_get_new_request:
            try:
                if self.is_https:
                    my_request = session.get(url=self.url, headers=headers, 
                        stream=self.stream, timeout=self.timeout, verify=self.sni_verify)
                else:
                    if self.specific_ip_address == None :
                        my_request = session.get(url=self.url, headers=headers, 
                            stream=self.stream, timeout=self.timeout)
                    else:
                        my_request = session.get(url=self.ip_direct_url, headers=headers, 
                            stream=self.stream, timeout=self.timeout)
            except (requests.Timeout, requests.ReadTimeout ) :
                session.close()
                retry_count = retry_count +1
                print(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"request time out.Retry count={retry_count * self.max_retries}")
                session = self.get_session_obj()
            except Exception as e:
                session.close()
                retry_count = retry_count +1
                print(f"request:my_thread_id={dp.my_thread_id}," + \
                    "unknow error.Retrying...error=",e)
                session = self.get_session_obj()
            else:
                break
        if need_return :
            return my_request
        dp.request_context = my_request
        dp.now_ready()

    # 默认每次拉取 10KiB 大小的数据块
    def download(self, dp:download_progress):
        dp.request_context = self.get_new_request(dp=dp, need_return=True)
        dp.now_ready()
        def is_not_finished()->bool:
            if dp.getsize_strict_level == level_enforce:
                return (dp.curr_start + dp.curr_getsize -1 != dp.curr_end)
            else:
                return (dp.curr_start + dp.curr_getsize -1 <  dp.curr_end )
        start_time = time.time()
        with open(self.full_path_to_file, "rb+") as f:
            f.seek(dp.curr_start)
            if (dp.chunk_size == 0):
                it = dp.request_context.iter_content()
            else:
                it = dp.request_context.iter_content( chunk_size=dp.chunk_size )
            dp.now_running()
            while dp.keep_run and is_not_finished():
                try:
                    chunk_data = next(it)
                    chunk_data_len = len(chunk_data)
                    curr_position = dp.curr_start + dp.curr_getsize -1
                    if (curr_position + chunk_data_len > dp.curr_end):
                        aim_len = dp.curr_end - curr_position
                        buffer = io.BytesIO(chunk_data)
                        chunk_data = buffer.read(aim_len)
                        print(f"worker:my_thread_id={dp.my_thread_id},"+\
                            f"chunk_size=\"{dp.chunk_size}\" is too huge."+\
                            f"curr_position={curr_position},"+\
                            f"chunk_data_len={chunk_data_len},"+\
                            f"dp.curr_end={dp.curr_end},"+\
                            "curr_position + chunk_data_len > dp.curr_end. " +\
                            f"Resize chunk data to size={len(chunk_data)}.")
                        dp.curr_getsize += aim_len
                        f.write(chunk_data)
                        break
                    else:
                        # 统计已下载的数据大小，单位是字节（byte）
                        dp.curr_getsize += chunk_data_len
                        f.write(chunk_data)
                        # f.flush()
                    # dp.curr_getsize += chunk_data_len
                    # f.write(chunk_data)
                except StopIteration:
                    it.close()
                    break
                except requests.ConnectionError as e:
                    print(f"worker:my_thread_id={dp.my_thread_id},known error=",e)
                    if( is_not_finished() ):
                        print(f"worker:my_thread_id={dp.my_thread_id},"+\
                            "did not finish yet.Retrying...")
                        self.chunk_download_retry(dp=dp)
                        if (dp.chunk_size == 0):
                            it = dp.request_context.iter_content()
                        else:
                            it = dp.request_context.iter_content( chunk_size=dp.chunk_size )
                        f.seek(dp.curr_start)
                        dp.now_running()
                except Exception as e:
                    print(f"worker:my_thread_id={dp.my_thread_id},unknow error=",e)
                    break
        if( is_not_finished() ):
            print(f"worker:my_thread_id={dp.my_thread_id}," + \
                f"start={dp.curr_start} + getsize={dp.curr_getsize} -1 != end={dp.curr_end}," + \
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
        # time.sleep(0.2)
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
            complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            print(f"downloaded: {complete_size_str:10} | process: {process:6.2f}% | speed: {speed}/s {' '*5}", end="\r")
            complete_size = sum(self.dp_list_truesize())
            if process >= 99 and complete_size == self.size:
                complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
                print(f"downloaded: {complete_size_str:10} | process: {100.00:6}% | speed:  0Byte/s ", end=" | ")
                break

    def download_watchdog(self):
        print("download_watchdog is running...")
        dp_list = self.download_progress_list
        while(sum( self.dp_list_truesize() ) < self.size):
            time.sleep(self.watchdog_frequent)
            for i in range(self.thread_num):
                # curr_dp = download_progress(dp_list[i]) 
                curr_dp = dp_list[i]
                ds = curr_dp.downloader_thread_status
                if ds == status_running :
                    if (curr_dp.curr_getsize > curr_dp.history_getsize) and \
                        (curr_dp.curr_getsize != 0):
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
                        try:
                            curr_dp.request_context.raw._fp.close()
                        except Exception:
                            pass
                elif ds == status_force_exit:
                    self.chunk_download_retry(dp=curr_dp)
                    print("watchdog:Submit a worker,"+\
                        f"my_thread_id={curr_dp.my_thread_id},"+\
                        f"start_from={curr_dp.curr_start}," + \
                        f"end_at={curr_dp.curr_end},"+\
                        f"total_work_load={self.get_humanize_size(curr_dp.get_curr_workload())}.")
                    try:
                        future = self.download_tp.submit(self.download,
                            dp=curr_dp, chunk_size=curr_dp.chunk_size )
                        # dp_list[i] = curr_dp
                        self.futures[i] = future
                    except Exception as e:
                        print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                            f"restart failed!Error=",e)
                    else:
                        print(f"watchdog:thread_id={curr_dp.my_thread_id},"+\
                            f"restart succeed!{' '*30}")

    def main(self)->bool:
        # print("Download mission overview:")
        # print()
        session = self.get_session_obj()
        # 发起URL请求，将response对象存入变量 r
        r = session.head( url=self.url, allow_redirects=True, verify=self.sni_verify)
        request_get_size_succeed_flag = False
        # 从回复数据获取文件大小
        if r.status_code == 200:
            self.size = int(r.headers["Content-Length"])
            request_get_size_succeed_flag = True
        elif self.stream: # 如果服务器不允许通过head请求探测资源大小
            session.close()
            session = self.get_session_obj()
            r = session.get(url=self.url, allow_redirects=True, verify=self.sni_verify, stream=True)
            it = r.iter_content(chunk_size=8)
            if r.status_code == 200:
                self.size = int(r.headers["Content-Length"])
                request_get_size_succeed_flag = True
        if request_get_size_succeed_flag:
            if (self.default_filename == self.filename or \
                self.filename == None or self.filename == ""):
                self.filename = self.get_file_name(url=self.url, response=r)
            r.close()
        else:
            r.close()
            print("File size request failed.Download canceled!")
            return False
        self.full_path_to_file = self.storage_root + self.filename
        print("Download file name=\"{}\"".format(self.filename))
        print("Download file size={}".format(self.get_humanize_size(self.size)))
        # 优先创建 size 大小的占位文件
        f = open(self.full_path_to_file, "wb")
        f.truncate(self.size)
        f.close()
        print("File space allocated.Starting download...")

        self.download_tp = ThreadPoolExecutor(max_workers=self.thread_num)
        start = 0
        part_size = int(self.size / self.thread_num)
        start_time = time.time()
        for i in range(self.thread_num):
            if(i+1 == self.thread_num):
                end = self.size -1
            else:
                end = (i+1) * part_size -1
            dp = download_progress(start=start, end=end, my_thread_id=i, chunk_size=256 )
            self.download_progress_list.append(dp)
            future = self.download_tp.submit(self.download, dp=dp)
            # future = tp.submit(self.download, start, end, my_thread_id=i )
            print(f"Submit a worker,my_thread_id={i},start_from={start},end_at={end},"+\
                f"total_work_load={self.get_humanize_size(dp.get_curr_workload())}")
            self.futures.append(future)
            start = end +1
        
        # TODO: 多线程动态断点续传，一个worker完成本职工作可以帮助另一个worker完成其工作
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
        def compute_sha256_hash():
            with open(self.full_path_to_file, "rb") as f:
                sha256_obj = hashlib.sha256()
                sha256_obj.update(f.read())
                hash_value = sha256_obj.hexdigest().upper()
                return hash_value
        if self.sha256_hash_value != None:
            print("Given sha256 hash value is   :" + self.sha256_hash_value)
            hash_value = compute_sha256_hash()
            print("Compute sha256 hash value is :" + hash_value)
            if (hash_value == self.sha256_hash_value):
                print("Hash matched!")
            else:
                print("Hash not match.Maybe file is broken.")
        else:
            print("Compute sha256 hash value is :" + compute_sha256_hash())
        return True

if __name__ == "__main__":
    thread_num = 8
    specific_ip_address = "1.0.0.66"
    # specific_ip_address = "1.0.0.100"
    # specific_ip_address = None
    url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    sha256_hash_value = "6182BB277CE268F10BCA7DB3A16B9475F75B7D861907C7EFB188A01420C5B780"
    # url = "https://speed.haoren.ml/cache.jpg"
    # sha256_hash_value = "A0D7DD06B54AFBDFB6811718337C6EB857885C489DA6304DAB1344ECC992B3DB"
    # url = "https://speed.cloudflare.com/__down?bytes=90"
    # sha256_hash_value = None
    # url = "http://127.0.0.1/download/text/123.txt"
    # sha256_hash_value = "3DCCBFEE56F49916C3264C6799174AF2FDDDEE75DD98C9E7EA5DF56C6874F0D7"
    down = downloader(
        url=url, 
        specific_ip_address=specific_ip_address, 
        thread_num=thread_num,
        sha256_hash_value=sha256_hash_value )
    down.main()
    

    
