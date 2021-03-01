import copy
import hashlib
import io
import os
import queue
import re
import requests
import sys
import time
import typing
import threading

from concurrent.futures import ThreadPoolExecutor
from urllib import parse
from requests.models import Response
from requests.sessions import HTTPAdapter
from forced_ip_https_adapter import ForcedIPHTTPSAdapter
from my_ram_io import LimitedBytearrayIO
from diy_thread_lock import my_thread_lock

from http import HTTPStatus
# download 线程状态常量
from my_const import STATUS_INIT
from my_const import STATUS_READY
from my_const import STATUS_RUNNING
from my_const import STATUS_WORK_FINISHED
from my_const import STATUS_EXIT
from my_const import STATUS_FORCE_EXIT
from my_const import STATUS_PAUSE

# 对 download 过程中的 getsize 约束程度
from my_const import LEVEL_ENFORCE      # 绝对精准，精准至byte级别
from my_const import LEVEL_PERMISSIVE   # 宽松，达量即可，允许超量

from my_const import DL_FIRST_BORN
from my_const import DL_INITIATING
from my_const import DL_RUNNING
from my_const import DL_COMPLETE
from my_const import DL_FAILED

# 最后一次代码修改时间
__updated__ = "2021-03-02 00:48:11"
__version__ = 0.5

# source code URL: https://blog.csdn.net/xufulin2/article/details/113803835

class download_progress:
    def __init__(self,
        start = 0, 
        end = 0, 
        my_thread_id:int=0, 
        chunk_size:int=512,
        getsize_strict_level=LEVEL_ENFORCE,
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
        self.running_status_tracker = None
        # 通过 hack 手段强行终止当前任务所需要的context
        self.response_context = None
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
        self.last_check_time = 0
        # 千真万确的实际累计下载大小
        self.history_done_size = 0
        # 这个worker目前的工作状态
        self.downloader_thread_status = STATUS_INIT
        self.my_future = None

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
        self.downloader_thread_status = STATUS_INIT
    
    def now_ready(self):
        self.downloader_thread_status = STATUS_READY

    def now_running(self):
        self.start_time = time.time()
        self.last_check_time = time.time()
        self.downloader_thread_status = STATUS_RUNNING

    def now_work_finished(self):
        self.downloader_thread_status = STATUS_WORK_FINISHED
        self.duration_count_up()
        self.end_time = time.time()
        try:
            self.response_context.close()
        except Exception:
            pass

    def now_exit(self):
        self.downloader_thread_status = STATUS_EXIT

    def now_force_exit(self):
        self.duration_count_up()
        self.downloader_thread_status = STATUS_FORCE_EXIT

class schedule_queue:
    def __init__(self, 
            dp_status):
        self.dp_status = dp_status
        self.queue:queue.Queue = queue.Queue()
        self.lock = my_thread_lock()

# class downloader_param:
#     def __init__(self, 
#             url:str, 
#             download_as_file:bool=True,
#             thread_num:int=4,
#             max_retries:int=0,
#             timeout_to_retry:float=3,
#             stream:bool=True,
#             sni_verify:bool=True,
#             use_watchdog:bool=True,
#             sha256_hash_value:str=None,
#             specific_ip_address:str=None,
#             specific_range:tuple=None,
#             **kwargs):
        
#         self.url:str = url
#         self.download_as_file:bool=download_as_file
#         self.thread_num:int=thread_num
#         self.max_retries:int=max_retries
#         self.timeout_to_retry:float=timeout_to_retry
#         self.stream:bool=stream
#         self.sni_verify:bool=sni_verify
#         self.use_watchdog:bool=use_watchdog
#         self.sha256_hash_value:str=sha256_hash_value
#         self.specific_ip_address:str=specific_ip_address
#         self.specific_range:tuple=specific_range
#         self.kwargs = kwargs

# source code URL: https://blog.csdn.net/qq_42951560/article/details/108785802
class downloader:
    ONE_OF_1024:float = 0.0009765625 # 1/1024
    default_filename:str = "url_did_not_provide_filename"
    """
    specific_range 要求传入的是一个长度为 2 的Tuple元组，包含(start_from, end_at)两个参数
    """
    def __init__(self, 
            url:str, 
            download_as_file:bool=True,
            thread_num:int=4,
            max_retries:int=0,
            timeout_to_retry:float=3,
            stream:bool=True,
            sni_verify:bool=True,
            use_watchdog:bool=True,
            sha256_hash_value:str=None,
            specific_ip_address:str=None,
            specific_range:tuple=None,
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
        self.download_to_ram = bytearray()
        self.filename:str = self.default_filename 
        self.storage_root:str = "downloads/"
        self.download_as_file:bool = download_as_file
        if self.download_as_file :
            if "filename" in kwargs:
                self.filename:str = str(kwargs.pop("filename"))
            if "storage_root" in kwargs:
                self.storage_root:str = str(kwargs.pop("storage_root"))
        self.full_path_to_file:str = self.storage_root + self.filename
        # 看门狗检查线程的频率，每多少秒检查一次
        self.watchdog_frequent = 5
        # 调度系统检查线程的频率，每多少秒检查一次
        self.schedule_frequent = 1
        # 设置超时时间，超出后立即重试
        self.timeout_to_retry = timeout_to_retry
        self.max_retries:int = max_retries
        self.stream:bool = stream
        self.sni_verify:bool = sni_verify
        self.thread_num:int = thread_num
        self.sha256_hash_value = None
        self.specific_ip_address = specific_ip_address
        self.use_watchdog:bool = use_watchdog
        self.specific_range:tuple = specific_range
        self.allow_print:bool = True
        self.watchdog_lock = my_thread_lock()

        if "allow_print" in kwargs:
            self.allow_print = bool(kwargs.pop("allow_print"))

        self.kwargs = kwargs

        self.response_with_content_length = None
        self.download_status = DL_FIRST_BORN
        self.download_tp = None
        self.download_progress_list:list = []
        self.status_running_queue:schedule_queue = \
            schedule_queue(dp_status=STATUS_RUNNING)
        self.status_exit_queue:schedule_queue = \
            schedule_queue(dp_status=STATUS_EXIT)
        self.status_force_exit_queue:schedule_queue = \
            schedule_queue(dp_status=STATUS_FORCE_EXIT)
        self.status_pause_queue:schedule_queue = \
            schedule_queue(dp_status=STATUS_PAUSE)
        self.status_other_queue:schedule_queue = \
            schedule_queue(dp_status=None)

        self.download_finished:bool = False

        if (sha256_hash_value != None):
            self.sha256_hash_value = sha256_hash_value.upper()
        if not os.path.exists(self.storage_root):
            os.makedirs(self.storage_root)
        

    def diy_output(self,*objects, sep=' ', end='\n', file=sys.stdout, flush=False):
        if self.allow_print:
            print(*objects, sep=sep, end=end, file=file, flush=flush)

    #source code URL:https://blog.csdn.net/mbh12333/article/details/103721834
    def get_file_name(self,url:str, response:Response)->str:
        filename = ''
        if response == None:
            self.response_with_content_length = self.get_response_with_content_length()
            response = self.response_with_content_length
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
        # dp.keep_run = True

    def get_session_obj(self)->requests.Session:
        session = requests.Session()
        if self.is_https:
            if self.specific_ip_address == None :
                session.mount(prefix="https://", adapter=ForcedIPHTTPSAdapter(max_retries=self.max_retries) )
            else:
                session.mount(prefix="https://" , adapter=ForcedIPHTTPSAdapter(max_retries=self.max_retries, dest_ip=self.specific_ip_address))
        else:
            session.mount(prefix="http://", adapter=HTTPAdapter(max_retries=self.max_retries) )
        return session

    def get_new_response(self, dp:download_progress):
        dp.now_init()
        if (dp.response_context != None):
            dp.response_context.close()
            dp.response_context = None
        headers = {
            "Host": self.hostname, 
            "User-Agent": self.user_agent }
        if self.stream :
            headers["Range"]= f"bytes={dp.curr_start}-{dp.curr_end}"
        session = self.get_session_obj()
        my_request = None
        retry_count = 0
        while True:
            try:
                if self.is_https:
                    my_request = session.get(url=self.url, headers=headers, 
                        stream=self.stream, timeout=self.timeout_to_retry, verify=self.sni_verify)
                else:
                    if self.specific_ip_address == None :
                        my_request = session.get(url=self.url, headers=headers, 
                            stream=self.stream, timeout=self.timeout_to_retry)
                    else:
                        my_request = session.get(url=self.ip_direct_url, headers=headers, 
                            stream=self.stream, timeout=self.timeout_to_retry)
                if dp.keep_get_new_request == False:
                    break
            except (requests.Timeout, requests.ReadTimeout ) as e :
                session.close()
                self.diy_output(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"request time out.Retry count={retry_count * (self.max_retries +1)}, " +\
                    "error=", e)
                if dp.keep_get_new_request == False:
                    break
                retry_count = retry_count +1
                session = self.get_session_obj()
            except Exception as e:
                session.close()
                self.diy_output(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"unknow error.Retry count={retry_count * (self.max_retries +1)}, " +\
                    "error=", e)
                if dp.keep_get_new_request == False:
                    break
                retry_count = retry_count +1
                session = self.get_session_obj()
            else:
                break
        dp.response_context = my_request
        if self.stream :
            if (dp.chunk_size == 0):
                dp.it = dp.response_context.iter_content()
            else:
                dp.it = dp.response_context.iter_content( chunk_size=dp.chunk_size )
        dp.now_ready()

    def get_file_io(self):
        f = None
        if self.download_as_file :
            f = open(self.full_path_to_file, "rb+")
        else:
            f = LimitedBytearrayIO(self.download_to_ram)
        return f


    # 下载文件的核心函数
    def download(self, dp:download_progress):
        self.get_new_response(dp=dp)
        is_enforce_mode:bool = True
        if dp.getsize_strict_level != LEVEL_ENFORCE:
            is_enforce_mode = False
        f = self.get_file_io()
        f.seek(dp.curr_start)
        # def save_data(data_in_bytearray, curr_pos):
        #     if self.download_as_file :
        #         f.write(data_in_bytearray)
        #     else:
        #         end = curr_pos + len(data_in_bytearray)
        #         f[curr_pos:end] = data_in_bytearray
        def is_not_finished()->bool:
            if is_enforce_mode:
                return (dp.curr_start + dp.curr_getsize -1 != dp.curr_end)
            else:
                return (dp.curr_start + dp.curr_getsize -1 <  dp.curr_end )

        dp.now_running()
        while dp.keep_run and is_not_finished():
            dp.is_need_to_pause()
            dp.running_status_tracker = 0
            try:
                chunk_data = next(dp.it)
                dp.running_status_tracker = 1
                chunk_data_len = len(chunk_data)
                curr_position = dp.curr_start + dp.curr_getsize
                dp.running_status_tracker = 2

                # For test
                # if dp.curr_getsize > 30000 and dp.curr_getsize < 40000:
                #     break

                if is_enforce_mode and (curr_position + chunk_data_len -1 > dp.curr_end):
                    dp.running_status_tracker = 3
                    aim_len = dp.curr_end - curr_position +1
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},"+\
                        f"maybe chunk_size=\"{dp.chunk_size}\" is too huge."+\
                        f"curr_position={curr_position},"+\
                        f"chunk_data_len={chunk_data_len},"+\
                        f"dp.curr_end={dp.curr_end},"+\
                        "curr_position + chunk_data_len > dp.curr_end. " +\
                        f"Resize chunk data to size={aim_len}.")
                    buffer = io.BytesIO(chunk_data)
                    dp.running_status_tracker = 4
                    chunk_data = buffer.read(aim_len)
                    buffer.close()
                    dp.running_status_tracker = 5
                    chunk_data_len = len(chunk_data)
                    dp.running_status_tracker = 6
                    dp.keep_run = False
                dp.running_status_tracker = 7
                # 统计已下载的数据大小，单位是字节（byte）
                dp.curr_getsize += chunk_data_len
                dp.running_status_tracker = 8
                # save_data(chunk_data, curr_position)
                f.write(chunk_data)
                dp.running_status_tracker = 9
                # f.flush()
            except StopIteration:
                dp.running_status_tracker = 10
                dp.it.close()
                dp.running_status_tracker = 11
                break
            except requests.ConnectionError as e:
                dp.running_status_tracker = 12
                self.diy_output(f"worker:my_thread_id={dp.my_thread_id},known error=",e)
                if( dp.keep_run and is_not_finished() ):
                    dp.running_status_tracker = 13
                    dp.now_init()
                    dp.running_status_tracker = 14
                    dp.duration_count_up()
                    dp.running_status_tracker = 15
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},"+\
                        "did not finish yet.Retrying...")
                    dp.running_status_tracker = 16
                    self.chunk_download_retry_init(dp=dp)
                    dp.running_status_tracker = 17
                    self.get_new_response(dp=dp)
                    dp.running_status_tracker = 18
                    # if self.download_as_file :
                    #     f.seek(dp.curr_start)
                    f.seek(dp.curr_start)
                    dp.running_status_tracker = 19
                    dp.now_running()
                    dp.running_status_tracker = 20
                else:
                    break
            except Exception as e:
                dp.running_status_tracker = 21
                self.diy_output(f"worker:my_thread_id={dp.my_thread_id},unknow error=",e)
                break
        f.close()
        # if self.download_as_file :
        #     f.close()
        # else:
        #     f.release()
        if( is_not_finished() ):
            dp.running_status_tracker = 22
            self.diy_output(f"worker:my_thread_id={dp.my_thread_id}," + \
                f"start={dp.curr_start} + getsize={dp.curr_getsize} -1 != end={dp.curr_end}," + \
                "exit abnormally.")
            dp.now_force_exit()
            dp.running_status_tracker = 23
            return None
        dp.running_status_tracker = 24
        dp.now_work_finished()
        tmp_curr_getsize = dp.curr_getsize
        dp.curr_getsize = 0
        dp.history_done_size += tmp_curr_getsize
        total_time = dp.duration
        total_size = dp.history_done_size
        average_speed = self.get_humanize_size(size_in_byte = total_size/total_time )
        self.diy_output(f"worker:my_thread_id={dp.my_thread_id},my job had done." +\
             f"Total downloaded: {self.get_humanize_size(total_size)}," +\
             f"total_time: {(total_time):.3f}s,"+ \
             f"average_speed: {average_speed}/s,"+ \
             f"retry_count: {dp.retry_count}")
        # time.sleep(0.2)
        dp.now_exit()

    # 自动转化字节数为带计算机1024进制单位的字符串
    def get_humanize_size(self, size_in_byte):
        size_in_byte = int(size_in_byte)
        if size_in_byte < 1024: # size under 1024 bytes (1KiB)
            return str(size_in_byte) + "byte"
        elif size_in_byte < 0x100000: # size under 1MiB (1048576 Bytes)
            result_num = (size_in_byte >> 10) + \
                ((size_in_byte & 0x3FF)*self.ONE_OF_1024 )
            return ("%.3f"%result_num) + "KiB"
        elif size_in_byte < 0x40000000: # size under 1GiB (1073741824 Bytes)
            result_num = (size_in_byte >> 20) + \
                (((size_in_byte & 0xFFC00) >> 10)*self.ONE_OF_1024 )
            return ("%.3f"%result_num) + "MiB"
        # size equal or greater than 1GiB... Wow!
        result_num = (size_in_byte >> 30) + \
                (((size_in_byte & 0x3FF00000) >> 20)*self.ONE_OF_1024 )
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
        return (sum( self.dp_list_truesize() ) == self.total_workload)

    def keep_run_until_download_finished(self, func, delay):
        while (not self.download_finished):
            func()
            time.sleep(delay)

    def update_download_finished_flag(self):
        self.download_finished = self.is_download_finished()

    def download_monitor_str(self):
        last_process = 0
        little_watchdog = time.time()
        while True:
            last = sum( self.dp_list_getsize() )
            time.sleep(1)
            curr = sum( self.dp_list_getsize() )
            complete_size = curr
            process = complete_size / self.total_workload * 100
            complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            self.diy_output(f"downloaded: {complete_size_str:10} | process: {process:6.2f}% | speed: {speed}/s {' '*5}", end="\r")
            if self.download_finished:
                complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
                self.diy_output(f"downloaded: {complete_size_str:10} | process: {100.00:6}% | speed:  0Byte/s ", end=" | ")
                break
            if last_process == process:
                if time.time() - little_watchdog > 5:
                    self.diy_output("Maybe something went wrong...")
            else:
                little_watchdog = time.time()
            last_process = process

    def schedule_dp_deliver(self, dp:download_progress):
        ds = dp.downloader_thread_status
        if ds == STATUS_RUNNING:
            self.status_running_queue.queue.put(dp)
            self.status_running_queue.lock.unlock()
        elif ds == STATUS_EXIT:
            self.status_exit_queue.queue.put(dp)
            self.status_exit_queue.lock.unlock()
        elif ds == STATUS_FORCE_EXIT:
            self.status_force_exit_queue.queue.put(dp)
            self.status_force_exit_queue.lock.unlock()
        elif ds == STATUS_PAUSE:
            self.status_pause_queue.queue.put(dp)
            self.status_pause_queue.lock.unlock()
        else:
            self.status_other_queue.queue.put(dp)
            self.status_other_queue.lock.unlock()

    def schedule_scan_dp_list(self):
        for dp in self.download_progress_list:
            self.schedule_dp_deliver(dp=dp)

    def schedule_status_running_queue(self):
        my_schedule_queue = self.status_running_queue
        while not my_schedule_queue.queue.empty():
            dp:download_progress = my_schedule_queue.queue.get()
            # TODO: do something but never put item back to queue
        my_schedule_queue.lock.block_myself()

    def schedule_status_force_exit_queue(self):
        my_schedule_queue = self.status_force_exit_queue
        while not my_schedule_queue.queue.empty():
            dp:download_progress = my_schedule_queue.queue.get()
            if dp.downloader_thread_status != STATUS_FORCE_EXIT:
                continue
            dp.now_init()
            self.chunk_download_retry_init(dp=dp)
            dp.keep_run = True
            self.diy_output("schedule:Resubmit a worker,"+\
                f"my_thread_id={dp.my_thread_id},"+\
                f"start_from={dp.curr_start}," + \
                f"end_at={dp.curr_end},"+\
                f"total_work_load={self.get_humanize_size(dp.get_curr_workload())}.")
            try:
                future = self.download_tp.submit(self.download, dp=dp )
                dp.my_future = future
            except Exception as e:
                self.diy_output(f"schedule:thread_id={dp.my_thread_id},"+\
                    f"resubmit failed!Error=",e)
            else:
                self.diy_output(f"schedule:thread_id={dp.my_thread_id},"+\
                    f"resubmit succeed!{' '*30}")
        my_schedule_queue.lock.block_myself()

    def schedule_status_exit_queue(self):
        my_schedule_queue = self.status_exit_queue
        while not my_schedule_queue.queue.empty():
            dp:download_progress = my_schedule_queue.queue.get()
            # TODO: do something but never put item back to queue
        my_schedule_queue.lock.block_myself()

    def schedule_status_pause_queue(self):
        my_schedule_queue = self.status_pause_queue
        while not my_schedule_queue.queue.empty():
            dp:download_progress = my_schedule_queue.queue.get()
            # TODO: do something but never put item back to queue
        my_schedule_queue.lock.block_myself()

    def schedule_status_other_queue(self):
        my_schedule_queue = self.status_other_queue
        while not my_schedule_queue.queue.empty():
            dp:download_progress = my_schedule_queue.queue.get()
            # TODO: do something but never put item back to queue
            # time.sleep(0.1)
        my_schedule_queue.lock.block_myself()

    def schedule_main(self):
        schedule_postman = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_scan_dp_list, 
                self.schedule_frequent ],
            daemon=True)
        schedule_postman.start()

        self.schedule_scan_dp_list()
        udff = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.update_download_finished_flag, 0.2 ],
            daemon=True)
        udff.start()

        ss_running = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_running_queue, 0 ],
            daemon=True)
        ss_exit = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_exit_queue, 0 ],
            daemon=True)
        ss_force_exit = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_force_exit_queue, 0 ], 
            daemon=True)
        ss_pause = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_pause_queue, 0 ],
            daemon=True)
        ss_other = threading.Thread(
            target=self.keep_run_until_download_finished, 
            args=[ self.schedule_status_other_queue, 0 ],
            daemon=True)
        
        ss_running.start()
        ss_exit.start()
        ss_force_exit.start()
        ss_pause.start()
        ss_other.start()

    def download_watchdog(self):
        self.diy_output("download_watchdog is running...")
        while( not self.download_finished ):
            time.sleep(self.watchdog_frequent)
            self.watchdog_lock.just_get_lock()
            for dp in self.download_progress_list :
                dp:download_progress
                if dp.downloader_thread_status != STATUS_RUNNING :
                    continue
                if (time.time() - dp.last_check_time < 5):
                    continue
                if (dp.curr_getsize > dp.history_getsize) and \
                    (dp.curr_getsize != 0):
                    dp.history_getsize = dp.curr_getsize
                    dp.last_check_time = time.time()
                    continue
                if dp.keep_run :
                    self.diy_output(f"watchdog:thread_id={dp.my_thread_id},"+\
                        f"had blocked over {self.watchdog_frequent} seconds!"+\
                        f"retry_count={dp.retry_count},Restarting...")
                    dp.keep_run = False
                else:
                    self.diy_output(f"watchdog:thread_id={dp.my_thread_id},"+\
                        "failed to terminate!"+\
                        f"tracker={dp.running_status_tracker},"+\
                        f"Retrying...{' '*30}")

                    try:
                        dp.response_context.raw._fp.close()
                        dp.hack_send_close_signal_count += 1
                    except Exception:
                        pass
                dp.last_check_time = time.time()
            self.watchdog_lock.unlock()
        self.diy_output("download_watchdog exited.")

    def get_response_with_content_length(self):
        session = self.get_session_obj()
        # 发起URL请求，将response对象存入变量 r
        r = session.head( url=self.url, allow_redirects=True, verify=self.sni_verify)
        headers = r.headers
        def content_length_exist():
            flag = (r.status_code == HTTPStatus.OK.value and \
                ("Content-Length" in headers) and headers["Content-Length"])
            return flag
        if content_length_exist():
            return r
        elif self.stream: # 如果服务器不允许通过head请求探测资源大小
            r.close()
            session = self.get_session_obj()
            r = session.get(url=self.url, allow_redirects=True, verify=self.sni_verify, stream=True)
            it = r.iter_content(chunk_size=8)
            if content_length_exist():
                return r
        # raise ValueError("unsupport response type.\"Content-Length\" is needed.")
        self.diy_output("unsupport response type.\"Content-Length\" is needed.")
        return None

    def download_range_init(self)->bool:
        self.diy_output("Sending request for URL detail...")
        start_time = time.time()
        # 从回复数据获取文件大小
        r = self.get_response_with_content_length()
        self.response_with_content_length = r
        took_time = "%.3f"%(time.time()-start_time)
        self.diy_output("Took {} seconds.".format(took_time) )
        if r == None:
            self.diy_output("File size request failed.Download canceled!")
            return False
        self.diy_output("Vaild response received.")
        self.origin_size = int(r.headers["Content-Length"])
        if self.specific_range == None:
            self.specific_range=(0, self.origin_size)
            self.total_workload = self.origin_size
            return True
        if isinstance(self.specific_range, tuple) and \
            len(self.specific_range) == 2 and \
            isinstance(self.specific_range[0], int) and \
            isinstance(self.specific_range[1], int):
                start = self.specific_range[0]
                end = self.specific_range[1]
                if start <= end and \
                    start < self.origin_size and \
                    end <= self.origin_size:
                    self.total_workload = end - start
                    return True
        self.diy_output("specific_range parameter is illegal!")
        return False
    
    def download_url_init(self):
        self.url_parse = parse.urlparse(url=self.url)
        self.hostname = self.url_parse.hostname
        self.is_https = False
        self.ip_direct_url = None
        if self.url_parse.scheme == "https":
            self.is_https = True
        if self.specific_ip_address != None and not self.is_https:
            pattern = re.compile(r"http://"+ re.escape(self.hostname) )
            self.ip_direct_url = re.sub(pattern, \
                repl="http://"+self.specific_ip_address ,string=self.url)

    def download_file_space_allocation(self):
        self.full_path_to_file = self.storage_root + self.filename
        if self.download_as_file:
            self.diy_output("Download file path=\"{}\"".format(self.full_path_to_file))
        else:
            self.diy_output("Download file path=\"RAM\"")
        self.diy_output("Download file origin size={}".format(self.get_humanize_size(self.origin_size)))
        self.diy_output("Download file size={}".format(self.get_humanize_size(self.total_workload)))
        self.diy_output("File space allocating...")
        start_time = time.time()
        if not os.path.exists(self.storage_root):
            os.makedirs(self.storage_root)
        if self.download_as_file:
            # 优先创建 size 大小的占位文件
            f = open(self.full_path_to_file, "wb")
            f.truncate(self.total_workload)
            f.close()
        else:
            # 优先占用 size 大小的RAM内存空间
            self.download_to_ram = bytearray(self.total_workload)
        took_time = "%.3f"%(time.time()-start_time)
        self.diy_output("Took {} seconds.".format(took_time) )
        self.diy_output("File space allocated.")

    def download_init(self)->bool:
        self.download_url_init()
        self.diy_output("Download URL="+self.url)
        self.diy_output("user_agent="+self.user_agent)
        if not self.download_range_init():
            return False
        # 初始化文件名，确保不空着
        if (self.default_filename == self.filename or \
            self.filename == None or self.filename == ""):
            self.filename = self.get_file_name(
                url=self.url, 
                response=self.response_with_content_length)
        self.download_file_space_allocation()
        return True

    def compute_sha256_hash(self)->str:
        file_data = None
        if self.download_as_file :
            with open(self.full_path_to_file, "rb") as file_stream:
                file_data = file_stream.read()
        else:
            file_data = self.download_to_ram
        sha256_obj = hashlib.sha256()
        sha256_obj.update(file_data)
        hash_value = sha256_obj.hexdigest().upper()
        return hash_value

    def main(self)->bool:
        # self.diy_output("Download mission overview:")
        if self.stream == False:
            self.just_get()
            return True
        self.download_status = DL_INITIATING
        if not self.download_init():
            self.download_status = DL_FAILED
            return False
        self.diy_output("Starting download...")
        self.download_tp = ThreadPoolExecutor(max_workers=self.thread_num)
        start = self.specific_range[0]
        total_workload = self.specific_range[1] - self.specific_range[0]
        part_size = int(total_workload / self.thread_num)
        self.download_status = DL_RUNNING
        start_time = time.time()
        for i in range(self.thread_num):
            if(i+1 == self.thread_num):
                end = self.specific_range[1] -1
            else:
                end = (i+1) * part_size -1
            dp = download_progress(start=start, end=end, my_thread_id=i, chunk_size=256 )
            self.download_progress_list.append(dp)
            future = self.download_tp.submit(self.download, dp=dp)
            self.diy_output(f"Submit a worker,my_thread_id={i},start_from={start},end_at={end},"+\
                f"total_work_load={self.get_humanize_size(dp.get_curr_workload())}")
            dp.my_future = future
            start = end +1
        
        self.schedule_main()
        # TODO: 多线程动态断点续传，一个worker完成本职工作可以帮助另一个worker完成其工作
        dms_thread = threading.Thread(target=self.download_monitor_str, daemon=True)
        dms_thread.start()
        if(self.use_watchdog):
            dw_thread = threading.Thread(target=self.download_watchdog, daemon=True)
            dw_thread.start()
        # self.diy_output("keep running")
        dms_thread.join()
        # self.download_monitor_str()
        end_time = time.time()
        self.download_tp.shutdown()
        total_time = end_time - start_time
        average_speed = self.get_humanize_size(size_in_byte = self.total_workload/total_time )
        self.diy_output(f"total-time: {total_time:.3f}s | average-speed: {average_speed}/s")

        if self.sha256_hash_value != None:
            self.diy_output("Given sha256 hash value is   :" + self.sha256_hash_value)
            hash_value = self.compute_sha256_hash()
            self.diy_output("Compute sha256 hash value is :" + hash_value)
            if (hash_value == self.sha256_hash_value):
                self.download_status = DL_COMPLETE
                self.diy_output("Hash matched!")
            else:
                self.download_status = DL_FAILED
                self.diy_output("Hash not match.Maybe file is broken.")
        else:
            self.download_status = DL_COMPLETE
            self.diy_output("Compute sha256 hash value is :" + self.compute_sha256_hash())
        return True

    def speedtest_download(self, dp:download_progress):
        self.get_new_response(dp=dp)
        def is_not_finished()->bool:
            return (dp.curr_start + dp.curr_getsize -1 != dp.curr_end)
        f = self.get_file_io()
        f.seek(dp.curr_start)
        dp.now_running()
        while dp.keep_run and is_not_finished():
            try:
                chunk_data = next(dp.it)
                chunk_data_len = len(chunk_data)
                dp.curr_getsize += chunk_data_len
                f.write(chunk_data)
            except StopIteration:
                dp.it.close()
                break
            except requests.ConnectionError as e:
                self.diy_output(f"worker:my_thread_id={dp.my_thread_id},known error=",e)
                break
            except Exception as e:
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},unknow error=",e)
                    break
        f.close()
        if( is_not_finished() ):
            self.diy_output(f"worker:my_thread_id={dp.my_thread_id}," + \
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
        self.diy_output(f"worker:my_thread_id={dp.my_thread_id},my job had done." +\
             f"Total downloaded:{self.get_humanize_size(total_size)}," +\
             f"total_time={(total_time):.3f}s,"+ \
             f"average_speed: {average_speed}/s,"+ \
             f"retry_count={dp.retry_count}")
        # time.sleep(0.2)
        dp.now_exit()

    def speedtest_countdown(self, 
            dp:download_progress, 
            timeout_to_stop ):
        ds = dp.downloader_thread_status
        while(ds == STATUS_INIT or ds == STATUS_READY):
            ds = dp.downloader_thread_status
            continue
        time.sleep(timeout_to_stop)
        if (dp.downloader_thread_status == STATUS_RUNNING):
            dp.response_context.raw._fp.close()

    def speedtest_single_thread(self, timeout_to_stop):
        if not self.download_init():
            return None
        self.diy_output("Debug version is running.")
        self.diy_output("Starting speedtest...")
        start = self.specific_range[0]
        end = self.specific_range[1] -1
        dp = download_progress(
            start=start, 
            end=end, 
            my_thread_id=0, 
            chunk_size=256 )
        self.download_progress_list = [dp]
        sc_thread = threading.Thread(
            target=self.speedtest_countdown, 
            args=[ dp, timeout_to_stop], 
            daemon=True)
        sc_thread.start()
        speedtest_thread = threading.Thread(
            target=self.speedtest_download,
            args=[ dp ], daemon=True)
        speedtest_thread.start()
        # self.speedtest_download(dp)
        speedtest_thread.join()
        return None

    def just_get(self, timeout_to_stop = None)->Response:
        self.download_status = DL_INITIATING
        self.stream = False
        if timeout_to_stop != None:
            self.timeout_to_retry = timeout_to_stop
        dp = download_progress()
        dp.keep_get_new_request = False
        self.download_progress_list = [ dp ]
        self.download_status = DL_RUNNING
        self.get_new_response(dp=dp)
        if dp.response_context == None:
            self.download_status = DL_FAILED
        else:
            self.download_status = DL_COMPLETE
        return dp.response_context

if __name__ == "__main__":
    thread_num = 32
    specific_ip_address = "1.0.0.0"
    # specific_ip_address = "1.0.0.100"
    # specific_ip_address = None
    sha256_hash_value = None
    specific_range = None
    # url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    # sha256_hash_value = "6182BB277CE268F10BCA7DB3A16B9475F75B7D861907C7EFB188A01420C5B780"
    url = "https://speed.haoren.ml/cache.jpg"
    # sha256_hash_value = "A0D7DD06B54AFBDFB6811718337C6EB857885C489DA6304DAB1344ECC992B3DB"
    # 128 MiB version
    sha256_hash_value = "45A3AE1D8321E9C99A5AEEA31A2993CF1E384661326C3D238FFAFA2D7451AEDB"
    specific_range = (0,134217728)
    # url = "https://speed.cloudflare.com/__down?bytes=92"
    # sha256_hash_value = None
    # url = "http://127.0.0.1/download/text/123.txt"
    # sha256_hash_value = "3DCCBFEE56F49916C3264C6799174AF2FDDDEE75DD98C9E7EA5DF56C6874F0D7"
    down = downloader(
        url=url, 
        specific_ip_address=specific_ip_address, 
        thread_num=thread_num,
        sha256_hash_value=sha256_hash_value,
        specific_range=specific_range,
        download_as_file=False,
        allow_print = True )
    down.main()
    

    
