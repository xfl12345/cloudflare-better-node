import multiprocessing.connection as mpc

import pings

def simple_mpc_ping(mp_pipe_sender:mpc.Connection, 
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

def simple_ping(
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
    return {ip_address:ping_result}