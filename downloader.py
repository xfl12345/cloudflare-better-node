from concurrent.futures import ThreadPoolExecutor
from requests import get, head
import time

# 源自：https://blog.csdn.net/qq_42951560/article/details/108785802

class downloader:
    def __init__(self, url, num, name):
        self.url = url
        self.num = num
        self.name = name
        self.getsize = [0]*self.num
        # 发起URL请求，将response对象存入变量 r
        r = head( url=self.url, allow_redirects=True)
        # 从回复数据获取文件大小
        self.size = int(r.headers['Content-Length'])

    # 默认每次拉取 10KiB 大小的数据块
    def download(self, start, end, my_thread_id=0, chunk_size=10240):
        start_time = time.time()
        headers = {'range': f'bytes={start}-{end}'}
        r = get(url=self.url, headers=headers, stream=True)
        with open(self.name, "rb+") as f:
            f.seek(start)
            for chunk in r.iter_content(chunk_size):
                f.write(chunk)
                # 统计已下载的数据大小，单位是字节（byte）
                self.getsize[my_thread_id] += chunk_size
        end_time = time.time()
        total_time = end_time - start_time
        total_size = end-start
        average_speed = self.get_humanize_size(size_in_byte = total_size/total_time )
        print(f"worker:my_thread_id={my_thread_id},my job had done." +\
             f"Total downloaded:{self.get_humanize_size(total_size)}," +\
             f"total_time={(total_time):.0f}s,"+ \
             f"average_speed: {average_speed}/s")

    # 自动转化字节数为带计算机1024进制单位的字符串
    def get_humanize_size(self, size_in_byte):
        size_in_byte = int(size_in_byte)
        # size under 1024 bytes (1KiB)
        if size_in_byte < 1024:
            return str(size_in_byte) + "bytes"
        elif size_in_byte < 1048576: # size under 1MiB
            result_num = (size_in_byte >> 10) + \
                ((size_in_byte & 0x3FF)/1024 )
            return ('%.3f'%result_num) + "KiB"
        elif size_in_byte < 1073741824: # size under 1GiB
            result_num = (size_in_byte >> 20) + \
                (((size_in_byte & 0xFFC00) >> 10)/1024 )
            return ('%.3f'%result_num) + "MiB"
        # size equal or greater than 1GiB... Wow!
        result_num = (size_in_byte >> 30) + \
                (((size_in_byte & 0x3FF00000) >> 20)/1024 )
        return ('%.3f'%result_num) + "GiB"
    
    def download_monitor(self):
        while True:
            last = sum(self.getsize)
            time.sleep(1)
            curr = sum(self.getsize)
            complete_size = curr
            process = complete_size / self.size * 100
            complete_size = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            print(f'downloaded: {complete_size:10} | process: {process:6.2f}% | speed: {speed}/s {" "*5}', end='\r')
            if process >= 100:
                print(f'downloaded: {complete_size:10} | process: {100.00:6}% | speed:  0.000KiB/s ', end=' | ')
                break

        return None

    def main(self):
        start_time = time.time()
        f = open(self.name, 'wb')
        # 优先创建 size 大小的占位文件
        f.truncate(self.size)
        f.close()
        tp = ThreadPoolExecutor(max_workers=self.num)
        futures = []
        start = 0
        for i in range(self.num):
            end = int((i+1)/self.num*self.size)
            future = tp.submit(self.download, start, end, my_thread_id=i, chunk_size=1024)
            print(f"Submit a worker,my_thread_id={i},start_from={start}," + \
                f"end_at={end},total_work_load={self.get_humanize_size(end-start)}")
            futures.append(future)
            start = end+1
        while True:
            last = sum(self.getsize)
            time.sleep(1)
            curr = sum(self.getsize)
            complete_size = curr
            process = complete_size / self.size * 100
            complete_size = self.get_humanize_size(size_in_byte = complete_size )
            speed = self.get_humanize_size(size_in_byte = (curr-last))
            print(f'downloaded: {complete_size:10} | process: {process:6.2f}% | speed: {speed}/s {" "*5}', end='\r')
            if process >= 100:
                print(f'downloaded: {complete_size:10} | process: {100.00:6}% | speed:  0.000KiB/s ', end=' | ')
                break
        tp.shutdown()
        end_time = time.time()
        total_time = end_time - start_time
        average_speed = self.get_humanize_size(size_in_byte = self.size/total_time )
        print(f'total-time: {total_time:.0f}s | average-speed: {average_speed}/s')


if __name__ == '__main__':
    url = "https://www.z4a.net/images/2018/07/09/-9a54c201f9c84c39.jpg"
    # url = 'https://cdn.111000333.xyz/dl/8MiB.img.jpg' # 8MiB.img.jpg
    down = downloader(url, 8, 'cdn_z4a.jpg')
    down.main()
