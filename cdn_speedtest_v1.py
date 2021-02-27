import re
import sys
import ipaddress
import multiprocessing as mp
import multiprocessing.connection as mpc

from cdn_downloader_v5 import downloader
from cdn_downloader_v5 import download_progress
import my_const

import pings
from forced_ip_https_adapter import ForcedIPHTTPSAdapter



# 最后一次代码修改时间
__updated__ = "2021-02-27 20:39:02"
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

    def get_cf_ipv4_netwrok_list(self, specific_ip_address:str="1.1.1.100"):
        down = downloader(
            url=self.ipv4_list_url, 
            download_as_file=False,
            specific_ip_address=specific_ip_address,
            stream = False
        )
        r = down.just_get()
        if r == None:
            del down
            return None
        pattern = re.compile(self.PATTERN_GET_NETWORK_ADDRESS)
        cf_network_address_iter = pattern.finditer(r.text)
        r.close()
        return [i.group(0) for i in cf_network_address_iter]

    def get_ipv4_nearby_endprefix(self, prefix:int)->int:
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

    def ping_scan_ipv4_subnetwork(self, 
            network_obj:ipaddress.IPv4Network, 
            end_prefixlen = None):
        if end_prefixlen == None:
            end_prefixlen = self.get_ipv4_nearby_endprefix(network_obj.prefixlen)
        else:
            end_prefixlen = int(end_prefixlen)
        if end_prefixlen < network_obj.prefixlen:
            raise ValueError(f"end_prefixlen={end_prefixlen}(24 in default) should not " +\
                f"smaller than network_obj's prefixlen={network_obj.prefixlen}.")
        sub_network_iter = network_obj.subnets( end_prefixlen - network_obj.prefixlen)
        ping_result_dict = {}
        # sub_network_address_list = []
        for i in sub_network_iter:
            ip_addr = str(i.network_address)
            # sub_network_address_list.append(ip_addr)
            ping_result_dict[ip_addr] = None
        self.diy_output(ping_result_dict)
        mp_receiver_list = []
        p_list = []

        for ip_address in ping_result_dict.keys():
            sender, receiver = mp.Pipe(duplex=True)
            p = mp.Process(target=self.mp_ping, args=(
                    sender,         #mp_pipe_sender
                    ip_address,     #ip_address
                    32,             #packet_data_size
                    400,            #timeout
                    0,              #max_wait
                    8               #times
                )
            )
            mp_receiver_list.append(receiver)
            p_list.append(p)
            p.start()
            # time.sleep(0.5)
        
        p_item:mp.Process
        for p_item in p_list:
            p_item.join()

        i:mpc.Connection
        for i in mp_receiver_list:
            res = dict(i.recv())
            ping_result_dict.update(res)
        
        return ping_result_dict



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
    res = test.get_cf_ipv4_netwrok_list()
    print(res)
    a_ipv4_network_obj = ipaddress.IPv4Network(res[11])
    ping_result_dict = test.ping_scan_ipv4_subnetwork(
        network_obj=a_ipv4_network_obj)
    ping_response:pings.response.Response
    for ip_addr, ping_response in ping_result_dict.items():
        print(ip_addr)
        for s in ping_response.messages:
            print(s)



