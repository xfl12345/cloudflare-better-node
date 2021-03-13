import re
import os
import sys
import time
import json
import ipaddress
import collections
import multiprocessing as mp
import multiprocessing.connection as mpc
import concurrent.futures as ccfutures

from forced_ip_https_adapter import ForcedIPHTTPSAdapter
from cdn_downloader_v5 import downloader
from cdn_downloader_v5 import download_progress
from ping_utils import simple_mpc_ping
from ping_utils import simple_ping
from diy_algorithm import BinarySearchTree, MergeSort
from cf_ddns import cf_simple_ddns
from dnspod_ddns import dnspod_simple_ddns

import pings
import my_const




# 最后一次代码修改时间
__updated__ = "2021-03-13 12:01:40"
__version__ = 0.1

class cloudflare_cdn_tool_utils:
    ipv4_list_url = "https://www.cloudflare.com/ips-v4"
    ipv6_list_url = "https://www.cloudflare.com/ips-v6"
    PATTERN_GET_IPV4_NETWORK_ADDRESS = r"(((1\d\d|2[0-4]\d|25[0-5]|[1-9]\d|\d)\.){3}(1\d\d|2[0-4]\d|25[0-5]|[1-9]\d|\d)/([12]\d|3[012]|\d))"

    file_root = "result/"
    simple_get_best_network_latest_result_filename = "latest_result.json"
    simple_get_best_network_latest_blackwhitelist_filename = "latest_blackwhitelist.json"


    def __init__(self, 
            allow_print:bool=False, 
            prefer_use_local_blackwhitelist:bool=True):
        self.allow_print:bool = allow_print
        self.prefer_use_local_blackwhitelist:bool=prefer_use_local_blackwhitelist

    # def clear_result(self):
    #     pass
    
    def diy_output(self,*objects, sep=' ', end='\n', file=sys.stdout, flush=False):
        if self.allow_print:
            print(*objects, sep=sep, end=end, file=file, flush=flush)

    def read_json_file(self, filename:str ):
        full_path_to_file = self.file_root + filename
        obj = None

        if not os.path.exists(self.file_root) or \
            not os.path.exists(full_path_to_file):
            return obj
        
        with open(full_path_to_file, "r", encoding='utf-8') as f:
            obj = json.loads(f.read())
        return obj

    def write_to_file(self, 
            filename=None, 
            curr_local_time=None, 
            content=None, 
            suffix:str=None):
        
        if not os.path.exists(self.file_root):
            os.makedirs(self.file_root)
        
        if filename == None:
            if curr_local_time == None:
                curr_local_time = self.get_curr_local_time()
            filename = time.strftime("%Y%m%d_%a_%H%M%S", curr_local_time)
        if suffix != None:
            filename = filename + "." + suffix
        filepath = self.file_root + filename
        f = open(file=filepath,
                mode="w",
                encoding="UTF-8")
        f.write(content)
        f.close()

    def write_obj_to_json_file(self,
            filename=None, 
            curr_local_time=None, 
            obj=None):
        
        content = json.dumps(
            obj=obj, 
            ensure_ascii=False, 
            indent=2 )

        suffix = None
        if filename != None:
            head, tail = os.path.splitext(filename)
            filename = head + ".json"
        else:
            suffix = "json"

        self.write_to_file(
            filename=filename,
            curr_local_time=curr_local_time,
            content=content, 
            suffix=suffix
        )

    def get_ipv4_netwrok_list(self, 
            specific_ip_address:str="1.1.1.100" ):
        pattern = re.compile(self.PATTERN_GET_IPV4_NETWORK_ADDRESS)
        if self.prefer_use_local_blackwhitelist:
            obj = self.read_json_file(
                filename=self.simple_get_best_network_latest_blackwhitelist_filename
            )
            netwrok_list = []
            if obj != None and isinstance(obj, list) and len(obj) == 2 and \
                    isinstance(obj[1], list) :
                for item in obj[1]:
                    if isinstance(item, str):
                        ip_addr_iter = pattern.finditer(item)
                        for i in ip_addr_iter:
                            netwrok_list.append(i.group(0))
                return netwrok_list

        down = downloader(
            url=self.ipv4_list_url, 
            download_as_file=False,
            specific_ip_address=specific_ip_address,
            stream = False,
            allow_print=self.allow_print
        )
        r = down.just_get_response()
        if r == None:
            del down
            return None
        cf_network_address_iter = pattern.finditer(r.text)
        r.close()
        return [i.group(0) for i in cf_network_address_iter]
  
    def get_ipv4_netwrok_nearby_endprefix(self, 
            prefix:int, 
            deep_level:int=my_const.SCAN_NORMAL)->int:
        def normal_or_default():
            a = (int(prefix / 4) + 1) *4
            if a > 32:
                return 32
            return a
        if deep_level == my_const.SCAN_NORMAL:
            return normal_or_default()
        elif deep_level == my_const.SCAN_MORE_DEEPER:
            if prefix < 8:
                return 16
            elif prefix < 16:
                return 24
            else:
                return 32
        elif deep_level == my_const.SCAN_DEEPEST:
            return 32
        else:
            return normal_or_default()

    def dict_mover(self, src:dict, dest:dict):
        # while True:
        #     try:
        #         k,v = src.popitem()
        #         dest[k] = v
        #     except KeyError:
        #         break
        dest.update(src)
        src.clear()

    def get_curr_local_time_str(self, curr_local_time:time.struct_time):
        return time.strftime("TZ(%z)_Y(%Y)-M(%m)-D(%d)_%A_%H:%M:%S", curr_local_time)

    def get_curr_local_time(self)->time.struct_time:
        return time.localtime()

    def ping_scan_ipv4_subnetwork(self, 
            network_obj:ipaddress.IPv4Network, 
            end_prefixlen=None, 
            ping_times:int=16,
            wirte_to_file:bool=False, 
            violence_mode:bool=False):
        if end_prefixlen == None:
            end_prefixlen = self.get_ipv4_netwrok_nearby_endprefix(network_obj.prefixlen)
        else:
            end_prefixlen = int(end_prefixlen)
        if end_prefixlen < network_obj.prefixlen:
            raise ValueError(f"end_prefixlen={end_prefixlen}(24 in default) should not " +\
                f"smaller than network_obj's prefixlen={network_obj.prefixlen}.")
        sub_network_iter = network_obj.subnets( end_prefixlen - network_obj.prefixlen)
        self.diy_output("ping_scan_ipv4_subnetwork:Starting ping...")
        start_time = time.time()

        curr_local_time = self.get_curr_local_time()
        curr_local_time_str = self.get_curr_local_time_str(curr_local_time=curr_local_time)
        ping_task_dict:dict = {
            "time":curr_local_time_str,
            "task_detail":{
                "function":"ping_scan_ipv4_subnetwork",
                "major_var":{
                    "network_address":network_obj.with_prefixlen,
                    "end_prefixlen":end_prefixlen,
                    "ping_times":ping_times
                },
                "duration_in_sec":0
            },
            "result":{
                "count":0,
                "reachable":{
                    "count":0, 
                    "lowest_loss_host":{
                        "count":0, 
                        "hosts":{}
                    },
                    "normal_host":{
                        "count":0, 
                        "hosts":{}
                    },
                },
                "unreachable":{
                    "count":0, 
                    "hosts":{}
                }
            }
        }
        ping_result_dict:dict = ping_task_dict["result"]
        reachable_dict:dict = ping_result_dict["reachable"]
        lowest_loss_host_dict:dict = reachable_dict["lowest_loss_host"]
        normal_host_dict:dict = reachable_dict["normal_host"]
        unreachable_dict:dict = ping_result_dict["unreachable"]
  
        ip_addr:str
        r:pings.ping.Response
        low_dict_last_key:str
        lowest_loss_host_dict["count"] = 0
        p_result_list = []
        p_result_list_len = 0

        if violence_mode:
            for item in sub_network_iter:
                ip_address = str(item.network_address)
                sender, receiver = mp.Pipe(duplex=True)
                p = mp.Process(
                    target=simple_mpc_ping, 
                    args=(
                        sender,         #mp_pipe_sender
                        ip_address,     #ip_address
                        32,             #packet_data_size
                        400,            #timeout
                        0,              #max_wait
                        ping_times      #times
                    )
                )
                p_result_list.append(
                    {
                        "p":p,
                        "receiver":receiver
                    }
                )
                p_result_list_len += 1
                p.start()
            # p_item:mp.Process
            # for p_item in p_list:
            #     p_item.join()
        else:
            max_task_num = os.cpu_count()
            if max_task_num != None:
                max_task_num = max_task_num << 1  # cpu_num = cpu_num *2
            else:
                max_task_num = 4
            # p_pool = ccfutures.ProcessPoolExecutor( max_workers=max_task_num )
            p_pool = ccfutures.ThreadPoolExecutor( max_workers=max_task_num )
            for item in sub_network_iter:
                ip_address = str(item.network_address)
                sender, receiver = mp.Pipe(duplex=True)
                p = p_pool.submit(
                    simple_ping,
                    ip_address=ip_address,  #ip_address
                    packet_data_size=32,    #packet_data_size
                    timeout=400,            #timeout
                    max_wait=0,             #max_wait
                    times=ping_times        #times
                )
                p_result_list.append(p)
                p_result_list_len += 1
            # p_asyncresult:mp.pool.AsyncResult
            # for p_asyncresult in p_asyncresult_list:
            #     p_asyncresult.wait()
        
        # 处理ping结果
        while p_result_list:
            if violence_mode:
                p_dict:dict = p_result_list.pop(0)
                p:mp.Process = p_dict["p"]
                if p.is_alive():
                    p_result_list.append(p_dict)
                    continue
                item_mpc:mpc.Connection = p_dict["receiver"]
                res = dict( item_mpc.recv() )
            else:
                item_ccfut:ccfutures.Future = p_result_list.pop(0)
                res = dict( item_ccfut.result() )
            ip_addr,r = res.popitem()
            ping_result_dict["count"] += 1
            # 如果主机可达
            if r.packet_received != 0:
                reachable_dict["count"] += 1
                # 如果 低丢包字典 不为空
                if lowest_loss_host_dict["count"] > 0: 
                    # 取出最后一次压入字典的值 并 强制类型为dict
                    d:dict = lowest_loss_host_dict["hosts"][low_dict_last_key]
                    # 取出字典里的 packet_loss 的值
                    dict_packet_loss = int(d["packet_loss"])
                    if dict_packet_loss >= int(r.packet_loss):
                        # 如果当前值是目前最小的，则倾倒字典到另一个字典
                        # 最少丢包的主机 可以不止一个 
                        if dict_packet_loss > int(r.packet_loss):
                            self.dict_mover(
                                src=lowest_loss_host_dict["hosts"],
                                dest=normal_host_dict["hosts"]
                            )
                            normal_host_dict["count"] += lowest_loss_host_dict["count"]
                            lowest_loss_host_dict["count"] = 0
                        lowest_loss_host_dict["hosts"][ip_addr] = r.to_dict()
                        low_dict_last_key = ip_addr
                        lowest_loss_host_dict["count"] += 1
                    else:
                        normal_host_dict["hosts"][ip_addr] = r.to_dict()
                        normal_host_dict["count"] += 1
                else: # lowest_loss_host_dict is empty
                    lowest_loss_host_dict["hosts"][ip_addr] = r.to_dict()
                    low_dict_last_key = ip_addr
                    lowest_loss_host_dict["count"] += 1
            else:
                unreachable_dict["hosts"][ip_addr] = r.to_dict()
                unreachable_dict["count"] += 1
            del ip_addr,r
        # for item in mp_receiver_list:
        #     res = dict(item.recv())
        #     ping_result_dict.update(res)
        took_time = time.time() - start_time
        ping_task_dict["task_detail"]["duration_in_sec"] = took_time
        self.diy_output("Took {} seconds.".format("%.3f"%took_time) )
        
        if wirte_to_file:
            self.write_obj_to_json_file(
                curr_local_time=curr_local_time,
                obj=ping_task_dict
            )

        if not violence_mode:
            p_pool.shutdown(wait=True)

        return ping_task_dict

    def simple_get_best_network(self, 
            wirte_to_file:bool=True, 
            get_blackwhitelist:bool=True,
            network_list:list=None):
        while network_list == None:
            network_list = self.get_ipv4_netwrok_list()
        start_time = time.time()
        curr_local_time = self.get_curr_local_time()
        curr_local_time_str = self.get_curr_local_time_str(curr_local_time=curr_local_time)
        ping_task_dict:dict={
            "time":curr_local_time_str,
            "task_detail":{
                "function":"simple_get_best_network_list",
                "major_var":{"get_blackwhitelist":get_blackwhitelist},
                "duration_in_sec":0
            },
            "result":{
                "count":0,
                "carefully_chosen":{
                    "count":0, 
                    "supernet":{
                        # "network_address":{   #This is fake code
                        #     "scan_deep":0     # the end_prefixlen
                        #     "count":0,
                        #     "subnetwork_address":{"ping_res_dict"}
                        # },
                    }
                },
                "unreachable":{
                    "count":0,
                    "supernet":{}
                }
            }
        }
        
        ping_result_dict:dict = ping_task_dict["result"]
        carefully_chosen_dict:dict = ping_result_dict["carefully_chosen"]
        unreachable_dict:dict = ping_result_dict["unreachable"]
        tmp_ping_result:dict
        curr_network_dict:dict
        copy_dict:dict

        for network_address in network_list:
            network_obj = ipaddress.IPv4Network(network_address)
            tmp_ping_result = self.ping_scan_ipv4_subnetwork(
                network_obj=network_obj, 
                ping_times=8, 
                wirte_to_file=False)
            
            is_complete_unreachable = True
            if tmp_ping_result["result"]["reachable"]["count"] != 0:
                is_complete_unreachable = False

                # 先把 顶级网络地址 作为 键值 创建出来
                carefully_chosen_dict["supernet"][network_address] = {}
                curr_network_dict = carefully_chosen_dict["supernet"][network_address]
                # 扫描深度（子网深度）
                curr_network_dict["scan_deep"] = tmp_ping_result["task_detail"]["major_var"]["end_prefixlen"]
                # 把旗下最优的子网网络ping结果全部照搬
                copy_dict = tmp_ping_result["result"]["reachable"]["lowest_loss_host"]
                curr_network_dict["count"] = copy_dict["count"]
                curr_network_dict["subnetwork_address"]={}
                self.dict_mover(
                    src=copy_dict["hosts"],
                    dest=curr_network_dict["subnetwork_address"]
                )
                # 纳入总数
                carefully_chosen_dict["count"] += copy_dict["count"]
                ping_result_dict["count"] += copy_dict["count"]
            
            # 同样办法处理不可达的网络，此处记录这些信息方便日后统计分析
            unreachable_dict["supernet"][network_address] = {}
            curr_network_dict = unreachable_dict["supernet"][network_address]
            curr_network_dict["is_complete_unreachable"] = is_complete_unreachable
            curr_network_dict["scan_deep"] = tmp_ping_result["task_detail"]["major_var"]["end_prefixlen"]
            copy_dict = tmp_ping_result["result"]["unreachable"]
            curr_network_dict["count"] = copy_dict["count"]
            curr_network_dict["subnetwork_address"]={}
            self.dict_mover(
                src=copy_dict["hosts"],
                dest=curr_network_dict["subnetwork_address"]
            )
            unreachable_dict["count"] += copy_dict["count"]
            ping_result_dict["count"] += copy_dict["count"]

        took_time = time.time() - start_time
        ping_task_dict["task_detail"]["duration_in_sec"] = took_time
        self.diy_output("Took {} seconds.".format("%.3f"%took_time) )

        if wirte_to_file:
            content = json.dumps(
                obj=ping_task_dict, 
                ensure_ascii=False, 
                indent=2 )
            self.write_to_file(
                filename=None,
                curr_local_time=curr_local_time,
                content=content,
                suffix="json")
            self.write_to_file(
                filename=self.simple_get_best_network_latest_result_filename,
                content=content)
        
        time.sleep(1)

        if get_blackwhitelist:
            blacklist = []
            whitelist = []
            blackwhitelist = [blacklist, whitelist]
            supernet_dict:dict = unreachable_dict["supernet"]
            subnet_addr_dict:dict
            for supernet in supernet_dict.keys():
                if bool(supernet_dict[supernet]["is_complete_unreachable"]):
                    blacklist.append(supernet)
                else:
                    subnet_addr_dict:dict = supernet_dict[supernet]["subnetwork_address"]
                    for subnetwork_address in subnet_addr_dict.keys():
                        network_address_with_prefix = subnetwork_address + \
                                        "/" + str(supernet_dict[supernet]["scan_deep"])
                        blacklist.append(network_address_with_prefix)
            
            supernet_dict = carefully_chosen_dict["supernet"]
            for supernet in supernet_dict.keys():
                subnet_addr_dict = supernet_dict[supernet]["subnetwork_address"]
                for subnetwork_address in subnet_addr_dict.keys():
                    network_address_with_prefix = subnetwork_address + \
                                    "/" + str(supernet_dict[supernet]["scan_deep"])
                    whitelist.append(network_address_with_prefix)
            
            if wirte_to_file:
                content = json.dumps(
                    obj=blackwhitelist, 
                    ensure_ascii=False, 
                    indent=2 )
                self.write_to_file(
                    curr_local_time=curr_local_time,
                    content=content,
                    suffix="json")
                self.write_to_file(
                    filename=self.simple_get_best_network_latest_blackwhitelist_filename,
                    content=content)
            return blackwhitelist

        return ping_task_dict

    def iteration_get_best_network(self, 
            wirte_to_file:bool=True, 
            get_blackwhitelist:bool=True, 
            iterate_times:int=1):
        res = None
        for i in range(iterate_times):
            res = self.simple_get_best_network(
                wirte_to_file=wirte_to_file,
                get_blackwhitelist=get_blackwhitelist
            )
        return res

class cloudflare_cdn_speedtest:
    """
    specific_range 要求传入的是一个长度为 2 的Tuple元组，包含(start_from, end_at)两个参数
    和 downloader 的 specific_range 参数相同
    """
    def __init__(self, 
            url:str, 
            download_as_file:bool=False,
            timeout_to_stop=20,  # float in second 
            allow_print:bool=False,
            sha256_hash_value:str=None,
            specific_range:tuple=my_const.SPEEDTEST_DEFAULT_RANGE ):
        self.url:str = url
        self.download_as_file:bool = download_as_file
        self.timeout_to_stop = timeout_to_stop
        self.allow_print:bool = allow_print
        self.sha256_hash_value = None
        if (sha256_hash_value != None):
            self.sha256_hash_value = sha256_hash_value.upper()
        self.specific_range = specific_range
        self.tool_utils = cloudflare_cdn_tool_utils(
            allow_print=allow_print
        )
        self.diy_output = self.tool_utils.diy_output

    def get_download_obj(self):
        return downloader(
            url=self.url,
            download_as_file=self.download_as_file,
            # allow_print=self.allow_print,
            allow_print=True,
            sha256_hash_value=self.sha256_hash_value,
            specific_range=self.specific_range
        )

    def just_speedtest(self, specific_ip_address:str, down:downloader=None):
        result_dict = {}
        if down == None:
            down = self.get_download_obj()
        down.specific_ip_address = specific_ip_address
        if not down.speedtest_single_thread(
                    result_dict=result_dict, 
                    timeout_to_stop=self.timeout_to_stop):
            res_dict = {
                "downloaded_size":0,
                "downloaded_size_h":"0Byte",
                "complete_download_count":0,
                "duration":0,
                "average_speed":0,
                "average_speed_h":"0Byte/s"
            }
            return res_dict
        
        complete_download_count:int = result_dict["complete_download_count"]
        total_size = result_dict["history_done_size"]
        total_time = result_dict["duration"]
        if total_time == 0:
            total_time = self.timeout_to_stop
        average_speed = total_size / total_time
        average_speed_humanize = \
            down.get_humanize_size(size_in_byte = average_speed ) + "/s"
        res_dict = {
            "downloaded_size":total_size,
            "downloaded_size_h":down.get_humanize_size(total_size),
            "complete_download_count":complete_download_count,
            "duration":total_time,
            "average_speed":average_speed,
            "average_speed_h":average_speed_humanize
        }
        return res_dict

    def just_test_1_0_0_0_p24_network(self):
        return self.just_test_network("1.0.0.0/24")

    def just_test_172_65_65_0_p24_network(self):
        return self.just_test_network("172.65.65.0/24")

    def just_test_172_64_100_0_p24_network(self):
        return self.just_test_network("172.64.100.0/24")

    def just_test_104_16_100_0_p24_network(self):
        return self.just_test_network("104.16.100.0/24")

    # def just_test_1_1_1_0_p24_network(self):
    #     return self.just_test_network("1.1.1.0/24")

    def just_test_network(self,ipv4_network_str:str):
        ipv4_network_obj = ipaddress.IPv4Network(ipv4_network_str)
        # sub_network_iter = ipv4_network_obj.subnets(new_prefix=32)
        # a_list = [ str(item.network_address) for item in sub_network_iter ]
        # print(a_list)
        curr_local_time = self.tool_utils.get_curr_local_time()
        curr_local_time_str = self.tool_utils.get_curr_local_time_str(
                    curr_local_time=curr_local_time)
        start_time = time.time()
        task_dict:dict={
            "time":curr_local_time_str,
            "task_detail":{
                "function":"just_test_network",
                "major_var":{
                    "ipv4_network_str":ipv4_network_str,
                    "test_hosts_count":0,
                },
                "duration_in_sec":0
            },
            "result":{
                "carefully_chosen":{
                    "count":0,
                    "hosts":collections.OrderedDict()
                },
                "normal":{
                    "count":0,
                    "hosts":{}
                },
                "unavailable":{
                    "count":0,
                    "hosts":[]
                }
            }
        }
        ping_task_dict = self.tool_utils.ping_scan_ipv4_subnetwork(
            network_obj=ipv4_network_obj,
            end_prefixlen=32,
            wirte_to_file=True,
            violence_mode=False
        )
        # print(ping_task_dict)
        unreachable_hosts = list((ping_task_dict["result"]["unreachable"]["hosts"]).keys())
        task_dict["result"]["unavailable"]["hosts"] += unreachable_hosts
        task_dict["result"]["unavailable"]["count"] += len(unreachable_hosts)
        hosts_dict:dict = ping_task_dict["result"]["reachable"]["lowest_loss_host"]["hosts"]
        hosts_iter = hosts_dict.keys()
        test_host_list = list(hosts_iter)
        normal_host_list = []

        down:downloader = self.get_download_obj()
        if self.sha256_hash_value == None and not down.main() :
            return False
        self.sha256_hash_value = down.sha256_hash_value
        down.specific_ip_address = test_host_list[0]
        down.speedtest_download_init()

        result_unavailable_dict = task_dict["result"]["unavailable"]
        result_normal_host_dict = task_dict["result"]["normal"]["hosts"]
        result_normal_dict = task_dict["result"]["normal"]
        total_download_size = 0
        total_download_time = 0.0
        total_average_speed = 0.0
        total_complete_download_count = 0
        for ip_address in test_host_list:
            tmp_speedtest_result = self.just_speedtest(
                specific_ip_address=ip_address, 
                down=down
            )
            if int(tmp_speedtest_result["downloaded_size"]) == 0:
                result_unavailable_dict["hosts"].append(ip_address)
                result_unavailable_dict["count"] += 1
                continue
            result_normal_host_dict[ip_address] = tmp_speedtest_result
            normal_host_list.append(ip_address)
            result_normal_dict["count"] += 1
            total_download_size += tmp_speedtest_result["downloaded_size"]
            total_download_time += tmp_speedtest_result["duration"]
            total_complete_download_count += tmp_speedtest_result["complete_download_count"]

        took_time = time.time() - start_time
        task_dict["task_detail"]["duration_in_sec"] = took_time
        self.diy_output("Took {} seconds.".format("%.3f"%took_time) )

        test_host_list_len = len(test_host_list)
        if test_host_list_len != 0 and result_unavailable_dict["count"] != test_host_list_len:
            total_average_speed = total_download_size / total_download_time
            average_complete_download_count = total_complete_download_count / test_host_list_len
        else:
            total_average_speed = 0
            average_complete_download_count = 0
        major_var_dict = task_dict["task_detail"]["major_var"]
        major_var_dict["test_hosts_count"] = test_host_list_len
        major_var_dict["total_download_size"] = total_download_size
        major_var_dict["total_download_time"] = total_download_time
        major_var_dict["total_average_speed"] = total_average_speed
        major_var_dict["total_complete_download_count"] = total_complete_download_count
        major_var_dict["average_complete_download_count"] = average_complete_download_count

        del test_host_list

        tmp_carefully_chosen_list = []
        def my_compare(a, b):
            if a["complete_download_count"] < b["complete_download_count"]:
                return True
            elif a["complete_download_count"] == b["complete_download_count"]:
                if a["average_speed"] < b["average_speed"]:
                    return True
                elif a["average_speed"] == b["average_speed"]:
                    if a["duration"] > b["duration"]:
                        return True
            return False
        merge_sort_obj = MergeSort(
            my_compare=my_compare
        )

        normal_host_list_len = len(normal_host_list)
        for i in range(0, normal_host_list_len):
            ip_address = normal_host_list.pop(0)
            curr_node_dict:dict = result_normal_host_dict[ip_address]
            curr_host_avg_speed = float(curr_node_dict["average_speed"])
            if curr_node_dict["complete_download_count"] >= average_complete_download_count and \
                    curr_host_avg_speed >= total_average_speed :
                    curr_node_dict["ip_address"] = ip_address
                    tmp_carefully_chosen_list.append(curr_node_dict)
                    del result_normal_host_dict[ip_address]
                    result_normal_dict["count"] -= 1
                    task_dict["result"]["carefully_chosen"]["count"] += 1

        merge_sort_obj.merge_sort(tmp_carefully_chosen_list)
        carefully_chosen_hosts_dict = task_dict["result"]["carefully_chosen"]["hosts"]
        while tmp_carefully_chosen_list:
            item:dict = tmp_carefully_chosen_list.pop(0)
            ip_address = item.pop("ip_address")
            carefully_chosen_hosts_dict[ip_address] = item
        
        self.tool_utils.write_obj_to_json_file(
            filename=None,
            curr_local_time=self.tool_utils.get_curr_local_time(),
            obj=task_dict
        )

        return task_dict
    

    def simple_update_ddns_to_a_better_node(self):
        # task_dict =self.tool_utils.read_json_file("20210310_Wed_173228.json")
        task_dict = self.just_test_172_64_100_0_p24_network()
        carefully_chosen_dict = task_dict["result"]["carefully_chosen"]
        if int(carefully_chosen_dict["count"]) == 0:
            self.diy_output("simple_update_ddns_to_a_better_node: " +\
                "All test node are unavailable.Mission failed.")
            return False
        carefully_chosen_hosts_dict = carefully_chosen_dict["hosts"]
        carefully_chosen_hosts_list = list(carefully_chosen_hosts_dict.keys())
        update_ip_address = carefully_chosen_hosts_list[0]
        self.diy_output("simple_update_ddns_to_a_better_node: "+\
                "Update DDNS to "+str(update_ip_address))
        if self.update_ddns(ipv4_address=carefully_chosen_hosts_list[0]):
            self.diy_output("simple_update_ddns_to_a_better_node: "+\
                "Update DDNS to a better node succeed!")
            return True
        return False



    def update_ddns(self, ipv4_address, choose="cf"):
        ddns_tools = None
        if choose == "cf":
            ddns_tools = cf_simple_ddns()
        else:
            ddns_tools = dnspod_simple_ddns()
        return ddns_tools.simple_update_ddns_domain_records(ipv4_address=ipv4_address)


    def main(self):

        # self.just_speedtest(str(self.network_address.next()))
        pass


if __name__ == "__main__":
    
    # url = "https://speed.haoren.ml/cache.jpg"
    # # 128 MiB version
    # sha256_hash_value = "45A3AE1D8321E9C99A5AEEA31A2993CF1E384661326C3D238FFAFA2D7451AEDB"
    # specific_range = (0,134217728)

    # url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    # sha256_hash_value = "6182BB277CE268F10BCA7DB3A16B9475F75B7D861907C7EFB188A01420C5B780"
    # specific_range = None

    url = "https://www.z4a.net/images/2017/07/20/myles-tan-91630.jpg"
    sha256_hash_value = "A58CB1B0ACF8435F0BD06FB04093875D75F15857DFC72F691498184DBA29BBED"
    specific_range = None

    test = cloudflare_cdn_speedtest(
        url=url,
        download_as_file=False,
        allow_print=True,
        sha256_hash_value=sha256_hash_value,
        specific_range=specific_range
    )
    test.simple_update_ddns_to_a_better_node()
    # print(test.just_speedtest("1.0.0.0"))
    # network_obj = ipaddress.IPv4Network("1.0.0.0/24")
    # test.tool_utils.ping_scan_ipv4_subnetwork(
    #     network_obj=network_obj,
    #     end_prefixlen=32,
    #     wirte_to_file=True,
    #     violence_mode=False
    # )
    

    

