import threading
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
            pass
            # print("my_thread_lock:error =",e)
        return False
