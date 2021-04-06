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
from diy_clock import base_chronograph, chronograph

from http import HTTPStatus
import my_const

# 最后一次代码修改时间
__updated__ = "2021-04-06 13:28:28"

# source code URL: https://blog.csdn.net/xufulin2/article/details/113803835
class download_progress:
    def __init__(self,
        start = 0, 
        end = 0, 
        my_thread_id:int=0, 
        chunk_size:int=512,
        getsize_strict_level=my_const.LEVEL_ENFORCE,
        exit_with_closed_response:bool=True,
        **kwargs):
        self.chunk_size = chunk_size
        self.getsize_strict_level = getsize_strict_level
        self.exit_with_closed_response = exit_with_closed_response
        # worker 最开始分配任务的起始和终点
        self.init_start = start
        self.init_end = end
        # start,end,getsize 仅仅表示当前任务状态
        # start: 起始下载位置，end: 终点下载位置
        # getsize: 当前任务的累计下载大小
        self.curr_start = start
        self.curr_end = end
        self.curr_getsize = 0
        self.running_status_tracker = None

        self.dp_chronograph = base_chronograph()    # dp chronograph
        self.dl_chronograph = base_chronograph()    # download chronograph
        self.rq_chronograph = base_chronograph()    # request for a response chronograph
        # 通过 hack 手段强行终止当前任务所需要的context
        self.response_context = None
        self.it = None  #response_context.iter_content
        # 控制一个 worker 持续循环接收数据的开关
        self.keep_run = True
        # TODO: 实现暂停功能
        # 使 worker 陷入暂停
        self.need_pause = False
        self.my_lock = my_thread_lock()
        # 是否不断坚持发起请求
        self.keep_get_request = True
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
        self.downloader_thread_status = my_const.STATUS_INIT
        self.my_future = None
        self.timer = time.time

    def is_need_to_pause(self):
        if self.need_pause:
            self.dp_chronograph.duration_count_up()
            self.my_lock.block_myself()
            self.dp_chronograph.set_start_time()

    def close_response_context(self):
        try:
            self.response_context.close()
        except Exception:
            pass

    def get_curr_workload(self):
        return self.curr_end - self.curr_start +1

    def now_init(self):
        self.downloader_thread_status = my_const.STATUS_INIT
    
    def now_ready(self):
        self.downloader_thread_status = my_const.STATUS_READY

    def now_running(self):
        tmp_time_val = self.dp_chronograph.set_start_time()
        self.last_check_time = self.timer()
        self.downloader_thread_status = my_const.STATUS_RUNNING
        return tmp_time_val

    # def now_pause(self):
    #     tmp_time_val = self.timer()
    #     self.downloader_thread_status = my_const.STATUS_PAUSE
    #     return tmp_time_val

    def now_work_finished(self):
        self.downloader_thread_status = my_const.STATUS_WORK_FINISHED
        tmp_time_val = self.dp_chronograph.end_and_count_up()
        if self.exit_with_closed_response:
            self.close_response_context()
        return tmp_time_val

    def now_exit(self):
        self.downloader_thread_status = my_const.STATUS_EXIT

    def now_force_exit(self):
        tmp_time_val = self.dp_chronograph.end_and_count_up()
        self.downloader_thread_status = my_const.STATUS_FORCE_EXIT
        if self.exit_with_closed_response:
            self.close_response_context()
        return tmp_time_val

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
        self.timer = time.time
        self.user_agent = my_const.USER_AGENT
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
        self.speedtest_download_init_is_ok:bool = False
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

        if "chunk_size" in kwargs:
            self.chunk_size = int(kwargs.pop("chunk_size"))
        else:
            self.chunk_size = 512 * my_const.ONE_BIN_KB

        self.kwargs = kwargs

        self.response_with_content_length = None
        self.download_status = my_const.DL_FIRST_BORN
        self.download_tp = None
        self.download_progress_list:list = []
        self.status_running_queue:schedule_queue = \
            schedule_queue(dp_status=my_const.STATUS_RUNNING)
        self.status_exit_queue:schedule_queue = \
            schedule_queue(dp_status=my_const.STATUS_EXIT)
        self.status_force_exit_queue:schedule_queue = \
            schedule_queue(dp_status=my_const.STATUS_FORCE_EXIT)
        self.status_pause_queue:schedule_queue = \
            schedule_queue(dp_status=my_const.STATUS_PAUSE)
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
            return str(self.timer())
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
                session.mount(prefix="https://", 
                        adapter=ForcedIPHTTPSAdapter(max_retries=self.max_retries) )
            else:
                session.mount(prefix="https://" , 
                        adapter=ForcedIPHTTPSAdapter(
                                    max_retries=self.max_retries, 
                                    dest_ip=self.specific_ip_address)  )
        else:
            session.mount(prefix="http://", 
                    adapter=HTTPAdapter(max_retries=self.max_retries) )
        return session

    def get_response_obj(self, 
            headers:dict, 
            session_obj:requests.Session )->requests.Response:
        my_response = None
        try:
            if self.is_https:
                my_response = session_obj.get(url=self.url, headers=headers, 
                    stream=self.stream, timeout=self.timeout_to_retry, verify=self.sni_verify)
            else:
                if self.specific_ip_address == None :
                    my_response = session_obj.get(url=self.url, headers=headers, 
                        stream=self.stream, timeout=self.timeout_to_retry)
                else:
                    my_response = session_obj.get(url=self.ip_direct_url, headers=headers, 
                        stream=self.stream, timeout=self.timeout_to_retry)
        except requests.Timeout as e :
            self.diy_output("get_response_obj:request time out.known error=", e)
        except Exception as e:
            self.diy_output("get_response_obj:request failed.unknow error=", e)
        return my_response

    def get_new_response_obj(self, 
            headers:dict,
            keep_request_dict:dict)->requests.Response:
        while True:
            session_obj = self.get_session_obj()
            response_obj = self.get_response_obj(session_obj=session_obj, headers=headers)
            if response_obj != None:
                return response_obj
            if not bool(keep_request_dict["keep_request"]):
                break
        return None

    def dp_get_new_response(self, dp:download_progress, status_control=True):
        if status_control:
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
        my_response = None
        retry_count = 0
        # 无论如何都必须执行一次get请求
        # 这是一个 do...while 语句
        dp.rq_chronograph.set_start_time()
        while True:
            try:
                if self.is_https:
                    my_response = session.get(url=self.url, headers=headers, 
                        stream=self.stream, timeout=self.timeout_to_retry, verify=self.sni_verify)
                else:
                    if self.specific_ip_address == None :
                        my_response = session.get(url=self.url, headers=headers, 
                            stream=self.stream, timeout=self.timeout_to_retry)
                    else:
                        my_response = session.get(url=self.ip_direct_url, headers=headers, 
                            stream=self.stream, timeout=self.timeout_to_retry)
                if dp.keep_get_request == False:
                    break
            except requests.Timeout as e :
                session.close()
                self.diy_output(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"request time out.Retry count={retry_count * (self.max_retries +1)}, " +\
                    "known error=", e)
                if not dp.keep_get_request:
                    break
                retry_count = retry_count +1
                session = self.get_session_obj()
            except Exception as e:
                session.close()
                self.diy_output(f"request:my_thread_id={dp.my_thread_id}," + \
                    f"request failed.Retry count={retry_count * (self.max_retries +1)}, " +\
                    "unknow error=", e)
                if not dp.keep_get_request:
                    break
                retry_count = retry_count +1
                session = self.get_session_obj()
            else:
                break
        dp.response_context = my_response
        dp.rq_chronograph.end_and_count_up()
        if my_response and self.stream :
            if (dp.chunk_size == 0):
                dp.it = dp.response_context.iter_content()
            else:
                dp.it = dp.response_context.iter_content( chunk_size=dp.chunk_size )
        if status_control:
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
        self.dp_get_new_response(dp=dp)
        if dp.response_context == None:
            self.diy_output(f"worker:my_thread_id={dp.my_thread_id}," + \
                "request failed. Exit abnormally.")
            dp.now_force_exit()
            return None
        is_enforce_mode:bool = True
        if dp.getsize_strict_level != my_const.LEVEL_ENFORCE:
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

        tmp_time_val = dp.now_running()
        dp.dl_chronograph.set_start_time(start=tmp_time_val)
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
                    # dp.keep_run = False
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
                    dp.dp_chronograph.duration_count_up()
                    dp.running_status_tracker = 15
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},"+\
                        "did not finish yet.Retrying...")
                    dp.running_status_tracker = 16
                    self.chunk_download_retry_init(dp=dp)
                    dp.running_status_tracker = 17
                    self.dp_get_new_response(dp=dp)
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
        dp.dl_chronograph.end_and_count_up()
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
        total_time = dp.dp_chronograph.duration
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
        # size under 1024 bytes (1KiB)
        if size_in_byte < my_const.ONE_BIN_KB: 
            return str(size_in_byte) + "byte"
        # size under 1MiB (1048576 Bytes)
        elif size_in_byte < my_const.ONE_BIN_MB: 
            result_num = (size_in_byte >> 10) + \
                ((size_in_byte & 0x3FF)*my_const.ONE_OF_1024 )
            return ("%.3f"%result_num) + "KiB"
        # size under 1GiB (1073741824 Bytes)
        elif size_in_byte < my_const.ONE_BIN_GB: 
            result_num = (size_in_byte >> 20) + \
                (((size_in_byte & 0xFFC00) >> 10)*my_const.ONE_OF_1024 )
            return ("%.3f"%result_num) + "MiB"
        # size equal or greater than 1GiB... Wow!
        result_num = (size_in_byte >> 30) + \
                (((size_in_byte & 0x3FF00000) >> 20)*my_const.ONE_OF_1024 )
        return ("%.3f"%result_num) + "GiB"

    def dp_list_getsize_iter(self):
        dp:download_progress
        for dp in self.download_progress_list:
            yield (dp.history_done_size + dp.curr_getsize)

    def dp_list_truesize_iter(self):
        dp:download_progress
        for dp in self.download_progress_list:
            yield dp.history_done_size

    def is_download_finished(self)->bool:
        return (sum( self.dp_list_truesize_iter() ) == self.total_workload)

    def keep_run_until_download_finished(self, func, delay):
        while (not self.download_finished):
            func()
            time.sleep(delay)

    def update_download_finished_flag(self):
        self.download_finished = self.is_download_finished()

    def download_monitor_str(self):
        last_process = 0
        little_watchdog = self.timer()
        while True:
            last = sum( self.dp_list_getsize_iter() )
            time.sleep(1)
            curr = sum( self.dp_list_getsize_iter() )
            complete_size = curr
            process = complete_size / self.total_workload * 100
            complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            self.diy_output(f"downloaded: {complete_size_str:10} | "+\
                    f"process: {process:6.2f}% | speed: {speed}/s {' '*5}", end="\r")
            if self.download_finished:
                complete_size_str = self.get_humanize_size(size_in_byte = complete_size )
                self.diy_output(f"downloaded: {complete_size_str:10} | "+\
                    f"process: {100.00:6}% | speed:  0Byte/s ", end=" | ")
                break
            if last_process == process:
                if self.timer() - little_watchdog > 5:
                    self.diy_output("Maybe something went wrong...")
            else:
                little_watchdog = self.timer()
            last_process = process

    def schedule_dp_deliver(self, dp:download_progress):
        ds = dp.downloader_thread_status
        if ds == my_const.STATUS_RUNNING:
            self.status_running_queue.queue.put(dp)
            self.status_running_queue.lock.unlock()
        elif ds == my_const.STATUS_EXIT:
            self.status_exit_queue.queue.put(dp)
            self.status_exit_queue.lock.unlock()
        elif ds == my_const.STATUS_FORCE_EXIT:
            self.status_force_exit_queue.queue.put(dp)
            self.status_force_exit_queue.lock.unlock()
        elif ds == my_const.STATUS_PAUSE:
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
            if dp.downloader_thread_status != my_const.STATUS_FORCE_EXIT:
                continue
            dp.now_init()
            self.chunk_download_retry_init(dp=dp)
            dp.keep_run = True
            dp.keep_get_request = True
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
                if dp.downloader_thread_status != my_const.STATUS_RUNNING :
                    continue
                if (self.timer() - dp.last_check_time < 5):
                    continue
                if (dp.curr_getsize > dp.history_getsize) and \
                    (dp.curr_getsize != 0):
                    dp.history_getsize = dp.curr_getsize
                    dp.last_check_time = self.timer()
                    continue
                dp.keep_get_request = False
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
                dp.last_check_time = self.timer()
            self.watchdog_lock.unlock()
        self.diy_output("download_watchdog exited.")

    def get_response_with_content_length(self):
        # 发起URL请求，将response对象存入变量 r
        def simple_request(use_stream:bool=False):
            request_succeed = False
            retry_count = 0
            while True:
                try:
                    if use_stream:
                        r = requests.get( url=self.url, 
                                        allow_redirects=True, 
                                        verify=self.sni_verify, 
                                        timeout=self.timeout_to_retry,
                                        stream=True)
                    else:
                        r = requests.head( url=self.url, 
                                        allow_redirects=True, 
                                        verify=self.sni_verify, 
                                        timeout=self.timeout_to_retry)
                except requests.Timeout as e :
                    self.diy_output("get_response_with_content_length:" + \
                        f"request time out.Retry count={retry_count}, " +\
                        "known error=", e)
                    retry_count += 1
                    if retry_count > self.max_retries:
                        break
                except Exception as e:
                    self.diy_output("get_response_with_content_length:" + \
                        f"request failed.Retry count={retry_count}, " +\
                        "unknow error=", e)
                    retry_count += 1
                    if retry_count > self.max_retries:
                        break
                else:
                    request_succeed = True
                    break
            if not request_succeed:
                self.diy_output("get_response_with_content_length:request failed.")
                return None
            return r
        def content_length_exist():
            headers = r.headers
            flag = (r.status_code == HTTPStatus.OK.value and \
                ("Content-Length" in headers) and headers["Content-Length"])
            return flag
        r = simple_request(use_stream=False)
        if r == None:
            return None
        if content_length_exist():
            return r
        elif self.stream: # 如果服务器不允许通过head请求探测资源大小
            r.close()
            r = simple_request(use_stream=True)
            it = r.iter_content(chunk_size=8)
            if content_length_exist():
                return r
        r.close()
        # raise ValueError("unsupport response type.\"Content-Length\" is needed.")
        self.diy_output("unsupport response type.\"Content-Length\" is needed.")
        return None

    def download_range_init(self)->bool:
        self.diy_output("Sending request for URL detail...")
        start_time = self.timer()
        # 从回复数据获取文件大小
        r = self.get_response_with_content_length()
        self.response_with_content_length = r
        took_time = "%.3f"%(self.timer()-start_time)
        self.diy_output("Took {} seconds.".format(took_time) )
        if r == None:
            self.diy_output("File size request failed.")
            return False
        self.diy_output("Vaild response received.")
        self.origin_size = int(r.headers["Content-Length"])
        if self.specific_range == None:
            self.specific_range = (0, self.origin_size -1)
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
                    self.total_workload = end - start +1
                    return True
        self.specific_range = (0, self.origin_size -1)
        self.total_workload = self.origin_size
        self.diy_output("specific_range parameter is illegal! "+\
            "Download size had been set to origin size.")
        return True
    
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
        if self.download_as_file:
            # 初始化文件名，确保不空着
            if (self.default_filename == self.filename or \
                self.filename == None or self.filename == ""):
                self.filename = self.get_file_name(
                    url=self.url, 
                    response=self.response_with_content_length)
            self.full_path_to_file = self.storage_root + self.filename
            self.diy_output("Download file path=\"{}\"".format(self.full_path_to_file))
        else:
            self.full_path_to_file = None
            self.diy_output("Download file path=\"RAM\"")
        self.diy_output("Download file origin size={}".format(self.get_humanize_size(self.origin_size)))
        self.diy_output("Download file size={}".format(self.get_humanize_size(self.total_workload)))
        self.diy_output("File space allocating...")
        start_time = self.timer()
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
        took_time = "%.3f"%(self.timer()-start_time)
        self.diy_output("Took {} seconds.".format(took_time) )
        self.diy_output("File space allocated.")
        self.file_space_allocated = True

    def download_init(self)->bool:
        self.diy_output("Download URL="+self.url)
        if self.specific_ip_address :
            self.diy_output("specific_ip_address="+self.specific_ip_address)
        self.diy_output("user_agent="+self.user_agent)
        self.download_url_init()
        if not self.download_range_init():
            return False
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
            self.just_get_response()
            return True
        self.download_status = my_const.DL_INITIATING
        if not self.download_init():
            self.download_status = my_const.DL_FAILED
            return False
        self.diy_output("Starting download...")
        self.download_tp = ThreadPoolExecutor(max_workers=self.thread_num)
        start = self.specific_range[0]
        total_workload = self.specific_range[1] - self.specific_range[0] +1
        part_size = int(total_workload / self.thread_num)
        self.download_status = my_const.DL_RUNNING
        start_time = self.timer()
        for i in range(self.thread_num):
            if(i+1 == self.thread_num):
                end = self.specific_range[1]
            else:
                end = (i+1) * part_size -1
            dp = download_progress(start=start, end=end, my_thread_id=i, chunk_size=self.chunk_size )
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
        end_time = self.timer()
        self.download_tp.shutdown()
        total_time = end_time - start_time
        average_speed = self.get_humanize_size(size_in_byte = self.total_workload/total_time )
        self.diy_output(f"total-time: {total_time:.3f}s | average-speed: {average_speed}/s")

        if self.sha256_hash_value != None:
            self.diy_output("Given sha256 hash value is   :" + self.sha256_hash_value)
            hash_value = self.compute_sha256_hash()
            self.diy_output("Compute sha256 hash value is :" + hash_value)
            if (hash_value == self.sha256_hash_value):
                self.download_status = my_const.DL_COMPLETE
                self.diy_output("Hash matched!")
            else:
                self.download_status = my_const.DL_FAILED
                self.diy_output("Hash not match.Maybe file is broken.")
        else:
            self.download_status = my_const.DL_COMPLETE
            self.sha256_hash_value = self.compute_sha256_hash()
            self.diy_output("Compute sha256 hash value is :" + self.sha256_hash_value)
        return True

    def speedtest_download(self, 
            dp:download_progress, 
            clock:chronograph, 
            result_dict:dict):
        def is_not_finished()->bool:
            return (dp.curr_start + dp.curr_getsize -1 != dp.curr_end)
        
        complete_download_count = 0
        first_hash_value = self.sha256_hash_value
        first_hash_value_is_none = False
        if first_hash_value == None:
            first_hash_value_is_none = True

        f = self.get_file_io()
        f.seek(dp.curr_start)
        self.dp_get_new_response(dp=dp)
        if dp.response_context == None:
            dp.keep_run = False
        end_time_val = None
        while dp.keep_run:
            tmp_time_val = dp.now_running()
            dp.dl_chronograph.set_start_time(start=tmp_time_val)
            while dp.keep_run:
                try:
                    chunk_data = next(dp.it)
                    chunk_data_len = len(chunk_data)
                    curr_position = dp.curr_start + dp.curr_getsize
                    if (curr_position + chunk_data_len -1 > dp.curr_end):
                        aim_len = dp.curr_end - curr_position +1
                        buffer = io.BytesIO(chunk_data)
                        chunk_data = buffer.read(aim_len)
                        buffer.close()
                        chunk_data_len = len(chunk_data)
                    dp.curr_getsize += chunk_data_len
                    f.write(chunk_data)
                except StopIteration:
                    if dp.it:
                        dp.it.close()
                    if is_not_finished():
                        self.diy_output(f"worker:my_thread_id={dp.my_thread_id}," +\
                        "known error=did not finish but could not download.")
                    break
                except requests.ConnectionError as e:
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},known error=",e)
                    break
                except Exception as e:
                    self.diy_output(f"worker:my_thread_id={dp.my_thread_id},unknow error=",e)
                    break
            clock.pause()
            end_time_val = dp.dl_chronograph.duration_count_up()
            dp.dp_chronograph.duration_count_up()
            f.seek(dp.curr_start)
            dp.history_done_size += dp.curr_getsize
            if not is_not_finished():
                complete_download_count += 1
                if first_hash_value == None:
                    first_hash_value = self.compute_sha256_hash()
                elif first_hash_value != self.compute_sha256_hash():
                    complete_download_count -= 1
                    break
            dp.curr_getsize = 0
            clock.go_on()
            self.dp_get_new_response(dp=dp, status_control=False)
        total_time = dp.dl_chronograph.duration
        clock.stop()
        f.close()
        dp.history_done_size += dp.curr_getsize
        if( is_not_finished() ):
            dp.now_force_exit()
        else:
            complete_download_count += 1
            if first_hash_value == None:
                first_hash_value = self.compute_sha256_hash()
            elif first_hash_value != self.compute_sha256_hash():
                complete_download_count -= 1
            dp.now_work_finished()
        if first_hash_value_is_none and complete_download_count == 1:
            complete_download_count = 0
        result_dict["duration"] = total_time
        result_dict["history_done_size"] = dp.history_done_size
        result_dict["complete_download_count"] = complete_download_count
        result_dict["curr_start"] = dp.curr_start
        result_dict["curr_end"] = dp.curr_end
        result_dict["total_workload"] = dp.curr_end - dp.curr_start
        result_dict["my_thread_id"] = dp.my_thread_id
        
        dp.dp_chronograph.end_and_count_up()
        total_size = dp.history_done_size
        if total_time == 0:
            average_speed = self.get_humanize_size(size_in_byte = 0)
        else:
            average_speed = self.get_humanize_size(size_in_byte = total_size/total_time )
        self.diy_output(f"worker:my_thread_id={dp.my_thread_id}," +\
             f"Total downloaded:{self.get_humanize_size(total_size)}," +\
             f"total_time={(total_time):.3f}s,"+ \
             f"average_speed: {average_speed}/s,"+ \
             f"complete_download_count={complete_download_count}")
        # time.sleep(0.2)
        dp.now_exit()
        return None

    def speedtest_countdown(self, 
            dp:download_progress, 
            clock:chronograph, 
            timeout_to_stop ):
        
        ds = dp.downloader_thread_status
        while(ds == my_const.STATUS_INIT or ds == my_const.STATUS_READY):
            time.sleep(0.1)
            ds = dp.downloader_thread_status
        clock.start()
        while(clock.duration < timeout_to_stop and ds == my_const.STATUS_RUNNING):
            time.sleep(0.1)
            ds = dp.downloader_thread_status
        clock.stop()
        if dp.downloader_thread_status == my_const.STATUS_RUNNING:
            dp.keep_run = False
            dp.keep_get_request = False
            self.diy_output(f"speedtest_countdown:dp my_thread_id={dp.my_thread_id},"+\
                "time is up.")
            if dp.response_context != None:
                dp.response_context.raw._fp.close()

    def speedtest_download_init(self)->bool:
        if not self.speedtest_download_init_is_ok:
            self.diy_output("Download URL="+self.url)
            self.diy_output("user_agent="+self.user_agent)
            self.download_url_init()
            if not self.download_range_init():
                return False
            self.download_file_space_allocation()
            self.speedtest_download_init_is_ok = True
        return True

    def speedtest_single_thread(self, 
            result_dict:dict, 
            timeout_to_stop=10):
        # 因为国内个人用户的 1Gbps 宽带普及率并不是很高
        # 所以假设 500Mbps 宽带可以超越全国 98% 的宽带用户
        # 经计算，欲1秒容纳500Mbps下行速率的全速下载
        # 需要准备 59.604644775390625 MiB 大小的内存缓冲
        # 拟定重复下载文件的 前 60MiB 大小分块
        # 若文件大小不足 60 MiB 则重复完整下载该文件
        self.download_as_file = False
        # self.use_watchdog = False
        self.max_retries = 1
        self.timeout_to_retry = 1
        if self.specific_range == None:
            self.specific_range = my_const.SPEEDTEST_DEFAULT_RANGE 
            
        if self.specific_ip_address :
                self.diy_output("specific_ip_address="+self.specific_ip_address)
        if not self.speedtest_download_init():
            return False
        self.diy_output("Debug version is running.")
        self.diy_output("Starting speedtest...")
        start = self.specific_range[0]
        end = self.specific_range[1]
        dp = download_progress(
            start=start, 
            end=end, 
            my_thread_id=0, 
            chunk_size=self.chunk_size )
        dp.keep_get_request = False
        self.download_progress_list = [dp]
        clock = chronograph()
        sc_thread = threading.Thread(
            target=self.speedtest_countdown, 
            args=[ dp, clock, timeout_to_stop ], 
            daemon=True)
        speedtest_thread = threading.Thread(
            target=self.speedtest_download,
            args=[ dp, clock , result_dict], 
            daemon=True)
        
        sc_thread.start()
        speedtest_thread.start()
        # self.speedtest_download(dp)
        speedtest_thread.join()
        sc_thread.join()
        return True

    def just_get_response(self, timeout_to_stop = None)->Response:
        self.download_status = my_const.DL_INITIATING
        self.stream = False
        if timeout_to_stop != None:
            self.timeout_to_retry = timeout_to_stop
        dp = download_progress()
        dp.keep_get_request = False
        self.download_progress_list = [ dp ]
        self.download_status = my_const.DL_RUNNING
        self.dp_get_new_response(dp=dp)
        if dp.response_context == None:
            self.download_status = my_const.DL_FAILED
        else:
            self.download_status = my_const.DL_COMPLETE
        return dp.response_context

if __name__ == "__main__":
    thread_num = 16
    specific_ip_address = "1.0.0.0"
    # specific_ip_address = "1.0.0.100"
    # specific_ip_address = None
    sha256_hash_value = None
    specific_range = None
    # url = "https://www.z4a.net/images/2017/07/20/myles-tan-91630.jpg"
    # sha256_hash_value = "A58CB1B0ACF8435F0BD06FB04093875D75F15857DFC72F691498184DBA29BBED"
    specific_range = None
    # url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    # sha256_hash_value = "6182BB277CE268F10BCA7DB3A16B9475F75B7D861907C7EFB188A01420C5B780"
    # url = "https://cf.xiu2.xyz/Github/CloudflareSpeedTest.png"
    # sha256_hash_value = "17A88AF83717F68B8BD97873FFCF022C8AED703416FE9B08E0FA9E3287692BF0"
    ###### 128 MiB version
    # specific_range = (0, 128 * my_const.ONE_BIN_MB -1)
    # sha256_hash_value = "254BCC3FC4F27172636DF4BF32DE9F107F620D559B20D760197E452B97453917"
    # ###### 60 MiB version
    # specific_range = (0, 60 * my_const.ONE_BIN_MB -1)
    # sha256_hash_value = "CF5AC69CA412F9B3B1A8B8DE27D368C5C05ED4B1B6AA40E6C38D9CBF23711342"

    url = "https://speed.cloudflare.com/__down?bytes=" + str(33 * my_const.ONE_BIN_MB)
    ###### 32 MiB version   __down?bytes=x, x>=32.5MiB
    sha256_hash_value = "34DBA6984A6EF54058F32C1B36CB5F62198B9926E67881A504B0042389D7E9B8"
    specific_range = (0, 32 * my_const.ONE_BIN_MB -1)
    
    # url = "http://127.0.0.1/download/text/123.txt"
    # sha256_hash_value = "3DCCBFEE56F49916C3264C6799174AF2FDDDEE75DD98C9E7EA5DF56C6874F0D7"
    down = downloader(
        url=url, 
        specific_ip_address=specific_ip_address, 
        thread_num=thread_num,
        sha256_hash_value=sha256_hash_value,
        specific_range=specific_range,
        download_as_file=True,
        chunk_size = 512 * my_const.ONE_BIN_KB,
        allow_print = True )
    down.main()
    

    
