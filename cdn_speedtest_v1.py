import re
import os
import sys
import time
import json
import ipaddress
import multiprocessing as mp
import multiprocessing.connection as mpc

from cdn_downloader_v5 import downloader
from cdn_downloader_v5 import download_progress
import my_const

import pings
from forced_ip_https_adapter import ForcedIPHTTPSAdapter



# 最后一次代码修改时间
__updated__ = "2021-02-28 23:14:53"
__version__ = 0.1

class cloudflare_cdn_tool_utils:
    ipv4_list_url = "https://www.cloudflare.com/ips-v4"
    ipv6_list_url = "https://www.cloudflare.com/ips-v6"
    PATTERN_GET_NETWORK_ADDRESS = r"(((1\d\d|2[0-4]\d|25[0-5]|[1-9]\d|\d)\.){3}(1\d\d|2[0-4]\d|25[0-5]|[1-9]\d|\d)/([12]\d|3[012]|\d))"
    def __init__(self, allow_print:bool=False):
        self.allow_print:bool = allow_print

    def diy_output(self,*objects, sep=' ', end='\n', file=sys.stdout, flush=False):
        if self.allow_print:
            print(*objects, sep=sep, end=end, file=file, flush=flush)

    def write_to_file(self, 
            curr_time=None, 
            content=None, 
            suffix:str="txt"):
        if curr_time == None:
            curr_time = time.localtime()
        file_root = "result/"
        if not os.path.exists(file_root):
            os.makedirs(file_root)
        filepath = file_root +\
            time.strftime("%Y%m%d_%a_%H%M%S", curr_time) + \
                "." + suffix
        f = open(file=filepath,
                mode="w",
                encoding="UTF-8"
            )
        f.write(content)
        f.close()

    def get_ipv4_netwrok_list(self, specific_ip_address:str="1.1.1.100"):
        down = downloader(
            url=self.ipv4_list_url, 
            download_as_file=False,
            specific_ip_address=specific_ip_address,
            stream = False,
            allow_print=self.allow_print
        )
        r = down.just_get()
        if r == None:
            del down
            return None
        pattern = re.compile(self.PATTERN_GET_NETWORK_ADDRESS)
        cf_network_address_iter = pattern.finditer(r.text)
        r.close()
        return [i.group(0) for i in cf_network_address_iter]

    def get_ipv4_netwrok_nearby_endprefix(self, prefix:int)->int:
        if prefix < 8:
            return 8
        elif prefix < 16:
            return 16
        elif prefix < 24:
            return 24
        else:
            return 32

    def mp_ping(self,
                mp_pipe_sender:mpc.Connection, 
                ip_address:str, 
                packet_data_size=32,# 32 bytes
                timeout=1000,       # 1000 ms
                max_wait=1000,      # 1000 ms
                times:int=4 ):
        ping_obj = pings.ping.Ping(
            quiet=True,
            packet_data_size=packet_data_size, 
            timeout=timeout,
            max_wait=max_wait
        )
        ping_result = ping_obj.ping(ip_address, times=times)
        mp_pipe_sender.send({ip_address:ping_result})
        return None

    def dict_mover(self, src:dict, dest:dict):
        while True:
            try:
                k,v = src.popitem()
                dest[k] = v
            except KeyError:
                break

    def get_curr_time_str(self, curr_time:time.struct_time):
        return time.strftime("TZ(%z)_Y(%Y)-M(%m)-D(%d)_%A_%H:%M:%S", curr_time)

    def ping_scan_ipv4_subnetwork(self, 
            network_obj:ipaddress.IPv4Network, 
            end_prefixlen=None, 
            ping_times:int=16,
            wirte_to_file:bool=False, 
            violence_mode:bool=True):
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

        mp_receiver_list = []

        if violence_mode:
            p_list = []
            for item in sub_network_iter:
                ip_address = str(item.network_address)
                sender, receiver = mp.Pipe(duplex=True)
                p = mp.Process(
                    target=self.mp_ping, 
                    args=(
                        sender,         #mp_pipe_sender
                        ip_address,     #ip_address
                        32,             #packet_data_size
                        400,            #timeout
                        0,              #max_wait
                        ping_times      #times
                    )
                )
                mp_receiver_list.append(receiver)
                p_list.append(p)
                p.start()
            # p_item:mp.Process
            # for p_item in p_list:
            #     p_item.join()
        else:
            max_task_num = os.cpu_count()
            if max_task_num != None:
                max_task_num = max_task_num << 1  # cpu_num = cpu_num *2
            # p_pool = mp.Pool(maxtasksperchild=max_task_num)
            p_pool = mp.Pool(processes=max_task_num)
            p_asyncresult_list = []
            for item in sub_network_iter:
                ip_address = str(item.network_address)
                sender, receiver = mp.Pipe(duplex=True)
                p = p_pool.apply_async(
                    func=self.mp_ping, 
                    args=(
                        sender,         #mp_pipe_sender
                        ip_address,     #ip_address
                        32,             #packet_data_size
                        400,            #timeout
                        0,              #max_wait
                        ping_times      #times
                    )
                )
                mp_receiver_list.append(receiver)
                p_asyncresult_list.append(p)
            # p_asyncresult:mp.pool.AsyncResult
            # for p_asyncresult in p_asyncresult_list:
            #     p_asyncresult.wait()

        curr_time = time.localtime()
        curr_time_str = self.get_curr_time_str(curr_time=curr_time)
        ping_task_dict:dict = {
            "time":curr_time_str,
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
                        "host":{}
                    },
                    "normal_host":{
                        "count":0, 
                        "host":{}
                    },
                },
                "unreachable":{
                    "count":0, 
                    "host":{}
                }
            }
        }
        ping_result_dict:dict = ping_task_dict["result"]
        reachable_dict:dict = ping_result_dict["reachable"]
        lowest_loss_host_dict:dict = reachable_dict["lowest_loss_host"]
        normal_host_dict:dict = reachable_dict["normal_host"]
        unreachable_dict:dict = ping_result_dict["unreachable"]
        
        item:mpc.Connection
        ip_addr:str
        r:pings.ping.Response
        low_dict_last_key:str
        lowest_loss_host_dict["count"] = 0
        for item in mp_receiver_list:
            res = dict(item.recv())
            ip_addr,r = res.popitem()
            ping_result_dict["count"] += 1
            # 如果主机可达
            if r.packet_received != 0:
                reachable_dict["count"] += 1
                # 如果 低丢包字典 不为空
                if lowest_loss_host_dict["count"] > 0: 
                    # 取出最后一次压入字典的值 并 强制类型为dict
                    d:dict = lowest_loss_host_dict["host"][low_dict_last_key]
                    # 取出字典里的 packet_loss 的值
                    dict_packet_loss = int(d["packet_loss"])
                    if dict_packet_loss >= int(r.packet_loss):
                        # 如果当前值是目前最小的，则倾倒字典到另一个字典
                        # 最少丢包的主机 可以不止一个 
                        if dict_packet_loss > int(r.packet_loss):
                            self.dict_mover(
                                src=lowest_loss_host_dict["host"],
                                dest=normal_host_dict["host"]
                            )
                            normal_host_dict["count"] += lowest_loss_host_dict["count"]
                            lowest_loss_host_dict["count"] = 0
                        lowest_loss_host_dict["host"][ip_addr] = r.to_dict()
                        low_dict_last_key = ip_addr
                        lowest_loss_host_dict["count"] += 1
                    else:
                        normal_host_dict["host"][ip_addr] = r.to_dict()
                        normal_host_dict["count"] += 1
                else: # lowest_loss_host_dict is empty
                    lowest_loss_host_dict["host"][ip_addr] = r.to_dict()
                    low_dict_last_key = ip_addr
                    lowest_loss_host_dict["count"] += 1
            else:
                unreachable_dict["host"][ip_addr] = r.to_dict()
                unreachable_dict["count"] += 1
            del ip_addr,r
        # for item in mp_receiver_list:
        #     res = dict(item.recv())
        #     ping_result_dict.update(res)
        took_time = time.time() - start_time
        ping_task_dict["task_detail"]["duration_in_sec"] = took_time
        self.diy_output("Took {} seconds.".format("%.3f"%took_time) )
        
        if wirte_to_file:
            content = json.dumps(
                obj=ping_task_dict, 
                ensure_ascii=False, 
                indent=2 )
            self.write_to_file(
                curr_time=curr_time,
                content=content,
                suffix="json")

        return ping_task_dict

    def simple_get_best_network_list(self, wirte_to_file:bool=True):
        network_list = None
        while network_list == None:
            network_list = self.get_ipv4_netwrok_list()
        start_time = time.time()
        curr_time = time.localtime()
        curr_time_str = self.get_curr_time_str(curr_time=curr_time)
        ping_task_dict:dict={
            "time":curr_time_str,
            "task_detail":{
                "function":"simple_get_best_network_list",
                "major_var":{},
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
        for network_address in network_list:
            network_obj = ipaddress.IPv4Network(network_address)
            tmp_ping_result = self.ping_scan_ipv4_subnetwork(
                network_obj=network_obj, 
                ping_times=8, 
                wirte_to_file=False)
            # 先把 顶级网络地址 作为 键值 创建出来
            carefully_chosen_dict["supernet"][network_address] = {}
            curr_network_dict:dict = carefully_chosen_dict["supernet"][network_address]
            # 扫描深度（子网深度）
            curr_network_dict["scan_deep"] = tmp_ping_result["task_detail"]["major_var"]["end_prefixlen"]
            # 把旗下最优的子网网络ping结果全部照搬
            copy_dict:dict = tmp_ping_result["result"]["reachable"]["lowest_loss_host"]
            curr_network_dict["count"] = copy_dict["count"]
            curr_network_dict["subnetwork_address"]={}
            self.dict_mover(
                src=copy_dict["host"],
                dest=curr_network_dict["subnetwork_address"]
            )
            # 纳入总数
            carefully_chosen_dict["count"] += copy_dict["count"]
            
            # 同样办法处理不可达的网络，此处记录这些信息方便日后统计分析
            unreachable_dict["supernet"][network_address] = {}
            curr_network_dict = unreachable_dict["supernet"][network_address]
            curr_network_dict["scan_deep"] = tmp_ping_result["task_detail"]["major_var"]["end_prefixlen"]
            copy_dict = tmp_ping_result["result"]["unreachable"]
            curr_network_dict["count"] = copy_dict["count"]
            curr_network_dict["subnetwork_address"]={}
            self.dict_mover(
                src=copy_dict["host"],
                dest=curr_network_dict["subnetwork_address"]
            )
            unreachable_dict["count"] += copy_dict["count"]

        took_time = time.time() - start_time
        ping_task_dict["task_detail"]["duration_in_sec"] = took_time
        self.diy_output("Took {} seconds.".format("%.3f"%took_time) )

        if wirte_to_file:
            content = json.dumps(
                obj=ping_task_dict, 
                ensure_ascii=False, 
                indent=2 )
            self.write_to_file(
                curr_time=curr_time,
                content=content,
                suffix="json")

        return ping_task_dict


class cloudflare_cdn_speedtest:
    def __init__(self, 
            download_obj:downloader,
            timeout_to_stop=10,
            **kwargs):
        self.download_obj = download_obj
        self.timeout_to_stop = timeout_to_stop

    def just_speedtest(self, specific_ip_address:str):
        down = self.download_obj
        down.speedtest_single_thread(timeout_to_stop=self.timeout_to_stop)
        dp:download_progress = down.download_progress_list[0]
        total_size = dp.curr_getsize
        total_time = dp.duration
        print(f"downloaded size: {down.get_humanize_size(total_size)}" )
        print(f"total_time: {total_time:3.f}" )
        average_speed = down.get_humanize_size(size_in_byte = total_size/total_time )
        print(f"average_speed: {average_speed}/s" )

        pass


    def main(self):

        # self.just_speedtest(str(self.network_address.next()))
        pass
    pass


if __name__ == "__main__":
    test = cloudflare_cdn_tool_utils()
    res = test.simple_get_best_network_list(wirte_to_file=True)
    print(res)



