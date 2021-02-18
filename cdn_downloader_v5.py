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

from concurrent.futures import ThreadPoolExecutor
from urllib import parse
from requests.models import Response
from requests.sessions import HTTPAdapter
from forced_ip_https_adapter import ForcedIPHTTPSAdapter

# 最后一次代码修改时间
__updated__ = "2021-02-18 22:01:22"
__version__ = 0.5

# download 线程状态常量
status_init = 0
status_ready = 1
status_running = 2
status_work_finished = 3
status_exit = 4
status_force_exit = 5
status_pause = 6
# 对 download 过程中的 getsize 约束程度
level_enforce = 0     # 绝对精准，精准至byte级别
level_permissive = 1  # 宽松，达量即可，允许超量

# source code URL: https://blog.csdn.net/xufulin2/article/details/113803835
class my_thread_lock:
    # wait_time = 0
    # allow_run = False
    # is_running = False
    # wait_time_count = 0

    def __init__(self):
        self.lock = threading.Lock()

    # 获取该锁是否已锁
    def is_locked(self):
        return self.lock.locked()

    # 不管怎么样，宁愿被阻塞，我就是要获取这个锁，
    # 因为我需要独享某个资源
    def just_get_lock(self):
        self.lock.acquire(blocking=True)
        return self.lock.locked()

    # 不管怎么样，宁愿被阻塞，也要锁上这个锁，
    # 哪怕我不是为了使用某个资源，只是为了锁定
    def just_lock(self):
        if( self.lock.locked() == False ):
            self.just_get_lock()
        return self.lock.locked()

    # 我不是为了锁资源的，而纯粹为了阻塞我自己，
    # 封印我自己，只为等到有人解除我的封印
    def block_myself(self):
        self.trylock()
        self.just_get_lock()
        self.unlock()
        return True

    # 尝试锁上，得逞了就得逞了，没得逞也不阻塞
    def trylock(self):
        if( self.lock.locked() ):
            return False
        if( self.lock.acquire(blocking=False) ):
            return self.lock.locked()
        return False

    def unlock(self):
        try:
            self.lock.release()
            return True
        except RuntimeError as e:
            print("my_thread_lock:error =",e)
        return False

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
        self.start_time = 0
        self.end_time = 0
        self.duration = 0
        # 通过 hack 手段强行终止当前任务所需要的context
        self.request_context = None
        self.it = None  #request_context.iter_content
        # 控制一个 worker 持续循环接收数据的开关
        self.keep_run = True
        # TODO: 实现暂停功能
        # 使 worker 陷入暂停
        self.need_pause = False
        self.my_lock = my_thread_lock()
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

    def duration_count_up(self):
        self.duration += time.time() - self.start_time

    def is_need_to_pause(self):
        if self.need_pause:
            self.duration_count_up()
            self.my_lock.block_myself()
            self.start_time = time.time()

    def get_curr_workload(self):
        return self.curr_end - self.curr_start +1

    def now_init(self):
        self.downloader_thread_status = status_init
    
    def now_ready(self):
        self.downloader_thread_status = status_ready

    def now_running(self):
        self.start_time = time.time()
        self.downloader_thread_status = status_running

    def now_work_finished(self):
        self.downloader_thread_status = status_work_finished
        self.duration_count_up()
        self.end_time = time.time()
        try:
            self.request_context.close()
        except Exception:
            pass

    def now_exit(self):
        self.downloader_thread_status = status_exit

    def now_force_exit(self):
        self.duration_count_up()
        self.downloader_thread_status = status_force_exit


# source code URL: https://blog.csdn.net/qq_42951560/article/details/108785802
class downloader:
    const_one_of_1024:float = 0.0009765625 # 1/1024
    default_filename:str = "url_did_not_provide_filename"
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
            use_watchdog:bool=True,
            **kwargs):
        my_python_version = str(sys.version_info.major) + "." +\
                    str(sys.version_info.minor) + "." +\
                    str(sys.version_info.micro) # + "_" +\
                    # str(sys.version_info.releaselevel) + "_" +\
                    # str(sys.version_info.serial)
        self.user_agent = "Python/" + my_python_version + " " +\
            "python_requests/" + str(requests.__version__) + " " +\
            f"cloudflare-better-node/{__version__} (github.com@xfl12345) "
        self.url:str = url
        self.filename:str = filename 
        self.storage_root:str = storage_root
        self.full_path_to_file:str = storage_root + filename
        # 看门狗检查线程的频率，每多少秒检查一次
        self.watchdog_frequent = 5
        # 调度系统检查线程的频率，每多少秒检查一次
        self.schedule_frequent = 1
        # 设置超时时间，超出后立即重试
        self.timeout = timeout
        self.max_retries:int = max_retries
        self.stream:bool = stream
        self.sni_verify:bool = sni_verify
        self.thread_num:int = thread_num
        self.sha256_hash_value = None
        self.specific_ip_address = specific_ip_address
        self.use_watchdog:bool = use_watchdog
        self.kwargs = kwargs

        self.download_tp = None
        self.download_progress_list:typing.List = []
        self.futures:typing.List = []
        self.status_running_queue:queue.Queue = queue.Queue()
        self.status_exit_queue:queue.Queue = queue.Queue()
        self.status_force_exit_queue:queue.Queue = queue.Queue()
        self.status_pause_queue:queue.Queue = queue.Queue()
        self.status_other_queue:queue.Queue = queue.Queue()

        self.running_queue_lock = my_thread_lock()
        self.download_finished:bool = False

        if (sha256_hash_value != None):
            self.sha256_hash_value = sha256_hash_value.upper()
        if not os.path.exists(self.storage_root):
            os.makedirs(self.storage_root)

        self.url_parse = parse.urlparse(url=url)
        self.hostname = self.url_parse.hostname
        self.is_https = False
        self.ip_direct_url = None
        
        if self.url_parse.scheme == "https":
            self.is_https = True
        
        if self.specific_ip_address != None and not self.is_https:
            pattern = re.compile(r"http://"+self.hostname)
            self.ip_direct_url = re.sub(pattern, \
                repl="http://"+self.specific_ip_address ,string=self.url)

    #source code URL:https://blog.csdn.net/mbh12333/article/details/103721834
    def get_file_name(self,url:str, response:Response)->str:
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
            return str(time.time())
        return filename

    def chunk_download_retry_init(self,dp:download_progress):
        part_of_done_size = int(dp.curr_getsize * 0.9)
        dp.history_done_size += part_of_done_size
        dp.curr_start = dp.curr_start + part_of_done_size
        dp.retry_count += 1
        dp.curr_getsize = 0
        dp.history_getsize = 0

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

    def get_new_request(self, dp:download_progress):
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
        dp.request_context = my_request
        if (dp.chunk_size == 0):
            dp.it = dp.request_context.iter_content()
        else:
            dp.it = dp.request_context.iter_content( chunk_size=dp.chunk_size )
        dp.now_ready()

    # 下载文件的核心函数
    def download(self, dp:download_progress):
        self.get_new_request(dp=dp)
        is_enforce_mode:bool = True
        if dp.getsize_strict_level != level_enforce:
            is_enforce_mode = False
        def is_not_finished()->bool:
            if is_enforce_mode:
                return (dp.curr_start + dp.curr_getsize -1 != dp.curr_end)
            else:
                return (dp.curr_start + dp.curr_getsize -1 <  dp.curr_end )
        with open(self.full_path_to_file, "rb+") as f:
            f.seek(dp.curr_start)
            dp.now_running()
            while dp.keep_run and is_not_finished():
                dp.is_need_to_pause()
                try:
                    chunk_data = next(dp.it)
                    chunk_data_len = len(chunk_data)
                    curr_position = dp.curr_start + dp.curr_getsize -1

                    # For test
                    # if dp.curr_getsize > 30000 and dp.curr_getsize < 40000:
                    #     break

                    if is_enforce_mode and (curr_position + chunk_data_len > dp.curr_end):
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
                except StopIteration:
                    dp.it.close()
                    break
                except requests.ConnectionError as e:
                    print(f"worker:my_thread_id={dp.my_thread_id},known error=",e)
                    if( is_not_finished() ):
                        dp.now_init()
                        dp.duration_count_up()
                        print(f"worker:my_thread_id={dp.my_thread_id},"+\
                            "did not finish yet.Retrying...")
                        self.chunk_download_retry_init(dp=dp)
                        self.get_new_request(dp=dp)
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
        dp.now_work_finished()
        tmp_curr_getsize = dp.curr_getsize
        dp.curr_getsize = 0
        dp.history_done_size += tmp_curr_getsize
        total_time = dp.duration
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
        dp:download_progress
        for dp in self.download_progress_list:
            yield (dp.history_done_size + dp.curr_getsize)
    
    def dp_list_truesize(self):
        dp:download_progress
        for dp in self.download_progress_list:
            yield dp.history_done_size

    def is_download_finished(self)->bool:
        return (sum( self.dp_list_truesize() ) == self.size)

    def keep_run_until_download_finished(self, func, delay):
        while (not self.download_finished):
            func()
            time.sleep(delay)

    def update_download_finished_flag(self):
        self.download_finished = self.is_download_finished()


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
            if self.download_finished:
                complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
                print(f"downloaded: {complete_size_str:10} | process: {100.00:6}% | speed:  0Byte/s ", end=" | ")
                break
        
    def schedule_dp_deliver(self, dp:download_progress):
        ds = dp.downloader_thread_status
        if ds == status_running:
            self.status_running_queue.put(dp)
        elif ds == status_exit:
            self.status_exit_queue.put(dp)
        elif ds == status_force_exit:
            self.status_force_exit_queue.put(dp)
        elif ds == status_pause:
            self.status_pause_queue.put(dp)
        else:
            self.status_other_queue.put(dp)

    def schedule_scan_dp_list(self):
        for dp in self.download_progress_list:
            self.schedule_dp_deliver(dp=dp)

    def schedule_status_running_queue(self):
        my_queue = self.status_running_queue
        while not my_queue.empty():
            if not self.running_queue_lock.trylock():
                break
            dp:download_progress = my_queue.get()
            if dp.downloader_thread_status != status_running :
                self.schedule_dp_deliver(dp=dp)
            else:
                my_queue.put(dp)
            self.running_queue_lock.unlock()

    def schedule_status_force_exit_queue(self):
        my_queue = self.status_force_exit_queue
        while not my_queue.empty():
            dp:download_progress = my_queue.get()
            if dp.downloader_thread_status != status_force_exit:
                self.schedule_dp_deliver(dp=dp)
                continue
            dp.now_init()
            self.chunk_download_retry_init(dp=dp)
            print("schedule:Resubmit a worker,"+\
                f"my_thread_id={dp.my_thread_id},"+\
                f"start_from={dp.curr_start}," + \
                f"end_at={dp.curr_end},"+\
                f"total_work_load={self.get_humanize_size(dp.get_curr_workload())}.")
            try:
                future = self.download_tp.submit(self.download, dp=dp )
                self.futures.append(future)
            except Exception as e:
                print(f"schedule:thread_id={dp.my_thread_id},"+\
                    f"resubmit failed!Error=",e)
            else:
                print(f"schedule:thread_id={dp.my_thread_id},"+\
                    f"resubmit succeed!{' '*30}")
            my_queue.put(dp)

    def schedule_status_exit_queue(self):
        my_queue = self.status_exit_queue
        while not my_queue.empty():
            dp:download_progress = my_queue.get()
            if dp.downloader_thread_status != status_exit:
                self.schedule_dp_deliver(dp=dp)
                continue
            my_queue.put(dp)
    
    def schedule_status_pause_queue(self):
        my_queue = self.status_pause_queue
        while not my_queue.empty():
            dp:download_progress = my_queue.get()
            if dp.downloader_thread_status != status_pause:
                self.schedule_dp_deliver(dp=dp)
                continue
            my_queue.put(dp)

    def schedule_status_other_queue(self):
        my_queue = self.status_other_queue
        while not my_queue.empty():
            dp:download_progress = my_queue.get()
            self.schedule_dp_deliver(dp=dp)
            time.sleep(0.5)

    def schedule_main(self):
        self.schedule_scan_dp_list()
        udff = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.update_download_finished_flag, 
                0.2 ],
            daemon=True)
        udff.start()
        ss_running = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_running_queue, 
                self.schedule_frequent ],
            daemon=True)
        ss_exit = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_exit_queue, 
                self.schedule_frequent ],
            daemon=True)
        ss_force_exit = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_force_exit_queue, 
                self.schedule_frequent ], 
            daemon=True)
        ss_pause = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_pause_queue, 
                self.schedule_frequent ],
            daemon=True)
        ss_other = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_other_queue, 
                0 ],
            daemon=True)
        ss_running.start()
        ss_exit.start()
        ss_force_exit.start()
        ss_pause.start()
        ss_other.start()
        
        


    def download_watchdog(self):
        print("download_watchdog is running...")
        my_queue = self.status_running_queue
        while( not self.download_finished ):
            time.sleep(self.watchdog_frequent)
            self.running_queue_lock.just_get_lock()
            while not my_queue.empty():
                dp:download_progress = my_queue.get()
                if dp.downloader_thread_status != status_running :
                    self.schedule_dp_deliver(dp=dp)
                    continue
                if (dp.curr_getsize > dp.history_getsize) and \
                    (dp.curr_getsize != 0):
                    dp.history_getsize = dp.curr_getsize
                    continue
                if dp.keep_run :
                    print(f"watchdog:thread_id={dp.my_thread_id},"+\
                        f"had blocked over {self.watchdog_frequent} seconds!"+\
                        f"retry_count={dp.retry_count},Restarting...")
                    dp.keep_run = False
                else:
                    print(f"watchdog:thread_id={dp.my_thread_id},"+\
                        f"failed to terminate!Retrying...{' '*30}")
                try:
                    dp.request_context.raw._fp.close()
                    dp.hack_send_close_signal_count += 1
                except Exception:
                    pass
                my_queue.put(dp)
            self.running_queue_lock.unlock()
        print("download_watchdog exited.")

    def get_response_with_content_length(self):
        session = self.get_session_obj()
        # 发起URL请求，将response对象存入变量 r
        r = session.head( url=self.url, allow_redirects=True, verify=self.sni_verify)
        headers = r.headers
        def content_length_exist():
            return (r.status_code == 200 and ("Content-Length" in headers) and headers["Content-Length"])
        if content_length_exist():
            return r
        elif self.stream: # 如果服务器不允许通过head请求探测资源大小
            r.close()
            r = session.get(url=self.url, allow_redirects=True, verify=self.sni_verify, stream=True)
            it = r.iter_content(chunk_size=8)
            if content_length_exist():
                return r
        return None

    def main(self)->bool:
        # print("Download mission overview:")
        print("user_agent="+self.user_agent)
        r = self.get_response_with_content_length()
        if r == None:
            print("File size request failed.Download canceled!")
            return False
        # 从回复数据获取文件大小
        self.size = int(r.headers["Content-Length"])
        # 初始化文件名，确保不空着
        if (self.default_filename == self.filename or \
            self.filename == None or self.filename == ""):
            self.filename = self.get_file_name(url=self.url, response=r)
        r.close()
        self.full_path_to_file = self.storage_root + self.filename
        print("Download file path=\"{}\"".format(self.full_path_to_file))
        print("Download file size={}".format(self.get_humanize_size(self.size)))
        print("File space allocating...")
        start_time = time.time()
        # 优先创建 size 大小的占位文件
        f = open(self.full_path_to_file, "wb")
        f.truncate(self.size)
        f.close()
        took_time = "%.3f"%(time.time()-start_time)
        print("File space allocated.Took {} seconds.".format(took_time),
            "Starting download...")

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
            print(f"Submit a worker,my_thread_id={i},start_from={start},end_at={end},"+\
                f"total_work_load={self.get_humanize_size(dp.get_curr_workload())}")
            self.futures.append(future)
            start = end +1
        
        self.schedule_main()
        # TODO: 多线程动态断点续传，一个worker完成本职工作可以帮助另一个worker完成其工作
        dms_thread = threading.Thread(target=self.download_monitor_str, daemon=True)
        dms_thread.start()
        if(self.use_watchdog):
            dw_thread = threading.Thread(target=self.download_watchdog, daemon=True)
            dw_thread.start()
        # print("keep running")
        dms_thread.join()
        # self.download_monitor_str()
        end_time = time.time()
        self.download_tp.shutdown()
        
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
    thread_num = 4
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
    

    
