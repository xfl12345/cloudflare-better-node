# download 线程状态常量
STATUS_INIT = 0
STATUS_READY = 1
STATUS_RUNNING = 2
STATUS_WORK_FINISHED = 3
STATUS_EXIT = 4
STATUS_FORCE_EXIT = 5
STATUS_PAUSE = 6
# 对 download 过程中的 getsize 约束程度
LEVEL_ENFORCE = 0     # 绝对精准，精准至byte级别
LEVEL_PERMISSIVE = 1  # 宽松，达量即可，允许超量