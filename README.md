# cloudflare-better-node

软件环境  

+ Python 3.8.5  
+ (Module) requests 2.24.0

## 介绍  

通过Python3来实现自动搜寻最快的Cloudflare CDN节点  
目前只完成了下载器功能  

## TODO  

+ [x] 下载器  
+ [ ] 多进程  
+ [ ] 循环遍历网段  
+ [ ] MySQL数据库联动  
+ [ ] 全自动DDNS  

## 进度

+ 下载器(cdn_downloader_v5.py)  
  origin code URL = <https://blog.csdn.net/qq_42951560/article/details/108785802>  
  + 已完成功能：  
    1. 不依赖pycurl包，不修改系统hosts文件，实现http/https访问域名时指定IPv4地址  
       powered by "forced_ip_https_adapter.py"  
       source code URL = <https://github.com/Roadmaster/forcediphttpsadapter/blob/master/forcediphttpsadapter/adapters.py>  
    2. 自动从URL截取或通过访问URL来获取未定义的下载文件名  
       source code URL = <https://blog.csdn.net/mbh12333/article/details/103721834>  
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
  Haven't started yet...

## 计划

Done is better than perfact.  
完成好过完美。  
是时候放弃“完美主义”  
敏捷开发告诉我们，迭代开发，虽然改动成本很高，  
但是它快且解渴啊！  
所以打算优先实现speedtest功能，回头再去开发downloader这个核心功能。  
因此downloader暂时告一段落了。。  
