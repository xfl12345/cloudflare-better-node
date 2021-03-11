# cloudflare-better-node

软件环境  

+ Python 3.8.5  
+ (Module) requests 2.24.0

## 介绍  

通过Python3来实现自动搜寻最快的Cloudflare CDN节点  

## 使用方法  

新建一个 `conf` 文件夹， `Cloudflare` 用户按 `cf_ddns.py` 里的顶部说明要求去补全信息，`DNSPOD` 用户按 `dnspod_ddns.py` 里的顶部说明要求去补全信息。  
然后在这个项目的根目录，直接运行命令 `python3 ./cdn_speedtest_v1.py` 就能实现一键全自动测速并DDNS啦！  

## TODO  

+ [x] 下载器  
+ [x] 多进程  
+ [x] 循环遍历网段（算法待优化，暂未启用）  
+ [ ] MySQL数据库联动  
+ [x] 全自动DDNS  

## 进度

+ 下载器(cdn_downloader_v5.py)  
  + 已完成功能：  
    1. 不依赖pycurl包，不修改系统hosts文件，实现http/https访问域名时指定IPv4地址  
    2. 自动从URL截取或通过访问URL来获取未定义的下载文件名  
    3. 多线程分段下载，精确到byte级别  
    4. 等待数据超时自动重新发起请求  
    5. watchdog看门狗。自动发现“僵尸线程”并重启问题线程  
       （所谓“僵尸线程”指的是“长时间下载速度为零的线程”）  
    6. 独立线程队列化处理不同状态的下载线程  
    7. 下载完成时自动计算sha256哈希值并反馈，若给定哈希值，则会进一步自动比较  
  + 未完成功能：  
    1. ETA 剩余时间计算  
    2. 智能调度。在已实现的队列系统进一步根据各下载线程的ETA剩余时间，做出是否派遣其它空闲线程帮忙下载，以达到所有线程一直都处于工作状态。  
    3. 暂停下载。利用 my_thread_lock 实现线程自我阻塞。  
    4. 断点续传  

+ CDN 节点暴力测速器(cdn_speedtest_v1.py)  
  + 已完成功能：  
    1. 自定义深度扫描IPv4网段  
    2. 在线拉取Cloudflare CDN节点 IPv4 网段列表  
    3. 读写JSON，测试结果可以保存到json文件，也可以从json文件读取结果  
    4. 基于ping测试的黑白名单  
    5. DNSPOD 中国区 DDNS
    6. Cloudflare DDNS  
    7. 无脑扫描特定网段并全自动DDNS  

## 计划

Done is better than perfact.  
完成好过完美。  
是时候放弃“完美主义”  
敏捷开发告诉我们，迭代开发，虽然改动成本很高，  
但是它快且解渴啊！  
所以打算优先实现speedtest功能，回头再去开发downloader这个核心功能。  
因此downloader暂时告一段落了。。  

## Credits  

This repo relies on the following third-party projects:  

+ In production:
  + [cdn_downloader_v5](cdn_downloader_v5.py)
    + [csdn/downloader](https://blog.csdn.net/qq_42951560/article/details/108785802)
    + [github/forced_ip_https_adapter](https://github.com/Roadmaster/forcediphttpsadapter/blob/master/forcediphttpsadapter/adapters.py)
    + [csdn/get_file_name](https://blog.csdn.net/mbh12333/article/details/103721834)
    + [csdn/diy_thread_lock](https://blog.csdn.net/xufulin2/article/details/113803835)
  + [cdn_speedtest_v1](cdn_speedtest_v1.py)
  + [github/pings](https://github.com/satoshi03/pings)
  + [github/headers](https://github.com/Narengowda/http_headers/blob/master/headers.py)
  + [github/dnspod_python](https://github.com/DNSPod/dnspod-python)
  + [csdn/json_tools](https://blog.csdn.net/xufulin2/article/details/114599569)

+ For testing only:
  + None
