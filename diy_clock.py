from sys import flags
import time
import threading
from diy_thread_lock import my_thread_lock

class base_chronograph:
    def __init__(self):
        self.start_time = 0
        self.end_time = 0
        self.duration = 0
        self.timer = time.time
    
    def set_start_time(self, start:float=None):
        tmp_time_value = self.timer()
        if start == None:
            self.start_time = tmp_time_value
        else:
            self.start_time = float(start)
        return tmp_time_value
    
    def set_end_time(self, end:float=None):
        tmp_time_value = self.timer()
        if end == None:
            self.end_time = tmp_time_value
        else:
            self.end_time = float(end)
        return tmp_time_value

    def set_duration(self, duration_val:float):
        self.duration = float(duration_val)
    
    def duration_count_up(self):
        tmp_time_value = self.timer()
        self.duration += (tmp_time_value - self.start_time)
        self.start_time = tmp_time_value
        return tmp_time_value

    def end_and_count_up(self):
        tmp_time_value = self.duration_count_up()
        self.end_time = tmp_time_value
        return tmp_time_value
    


# 该 chronograph 仅支持单线程使用（即每个函数只能同时调用一次）
class chronograph(base_chronograph):
    def __init__(self):
        base_chronograph.__init__(self)
        self.my_thread = None
        self.delay_func = time.sleep
        self.need_pause = False
        self.need_stop = False
        self.my_lock = my_thread_lock()


    def start(self):
        # 不可以多线程哦
        if (self.my_thread != None):
            self.go_on()
            return None
        self.need_stop = False
        self.need_pause = False
        self.duration = 0
        
        self.my_thread = threading.Thread(
            target=self.run
        )
        self.my_thread.start()

    def stop(self):
        self.need_stop = True
        # 如果处于被暂停状态下
        if self.my_lock.is_locked():
            self.my_lock.unlock()
            self.delay_func(0.1)
        self.my_thread = None

    def restart(self):
        self.stop()
        self.start()

    def go_on(self):
        self.need_pause = False
        self.my_lock.unlock()

    def pause(self):
        self.need_pause = True

    def run(self):
        self.start_time = self.timer()
        while(not self.need_stop):
            self.duration_count_up()
            if self.need_pause :
                self.my_lock.block_myself()
                self.start_time = self.timer()

