# -*- coding:utf-8 -*-


import json
import dnspod_apicn as apicn

import conf.dnspod_ddns_conf as dnspod_conf

"""

更多信息请访问：
https://support.dnspod.cn/Kb/showarticle/tsid/227/

"conf" 文件夹下的 "dnspod_ddns_conf.py" 文件必须包含如下内容:

# This is a example.
login_id = "1234567"
login_token = login_id + "," + "s5a1d6a5s1d56s1ad6a1sd5asd1"
domain = "example@example.com"
aim_subdomain = "diy_a_subdomain"

从 https://support.dnspod.cn/Kb/showarticle/tsid/227 里截取的部分资料：
login_token 需要用 ID 和 Token 这两个字段来组合成一个完整的 Token，组合方式为："ID,Token"（用英文半角逗号分割），比如 ID 为：13490,ToKen为：6b5976c68aba5b14a0558b77c17c3932。即完整的 Token 为：13490,6b5976c68aba5b14a0558b77c17c3932 。得到完整的 Token 之后，调用方式如下：

curl https://dnsapi.cn/Domain.List -d "login_token=13490,6b5976c68aba5b14a0558b77c17c3932&format=json"

"""

class dnspod_simple_ddns:
    # please refer to:
    # https://support.dnspod.cn/Kb/showarticle/tsid/227/
    def __init__(self) -> None:
        pass
    def simple_update_ddns_domain_records(self, ipv4_address:str):
        print("DomainInfo")
        api = apicn.DomainInfo(domain=dnspod_conf.domain, domain_id="", login_token=dnspod_conf.login_token, lang="en")
        api_response_data = api()
        # print("api_response=",api_response)
        # print("domain_data=",api_response.get("domain"))
        domain_id = api_response_data.get("domain").get("id")
        print("Domain \"%s\"'s id is \"%s\". " % (dnspod_conf.domain, domain_id))

        print("RecordList")
        api = apicn.RecordList( domain_id=domain_id, login_token=dnspod_conf.login_token, record_type="A",lang="en")
        api_response_data = api()
        # print("Type of api_response_data=",type(api_response_data))
        # print(api_response_data)

        records_domain = str(api_response_data.get("domain")["name"])
        records_num = int(api_response_data.get("info")["records_num"])
        records_data = api_response_data.get("records")

        aim_records_subdomain = dnspod_conf.aim_subdomain
        aim_records_id = None

        # 默认遍历的全是A记录
        for i in range( 0, records_num ):
            records_subdomain = str(records_data[i]["name"])
            records_ip_address = str(records_data[i]["value"])
            records_id = int(records_data[i]["id"])
            print("Domain \"%s.%s\"'s id is \"%s\",ip adress is \"%s\". " % (records_subdomain, 
                records_domain, records_id, records_ip_address) )
            # 获取子域名"myfastcdnnode"的 record_id
            if( records_subdomain == aim_records_subdomain ):
                aim_records_id = records_id
        print("RecordDdns")
        print("Update domain \"%s.%s\" to \"%s\"" % (aim_records_subdomain, \
                records_domain, ipv4_address ) )
        api = apicn.RecordDdns(record_id = aim_records_id, \
            sub_domain = aim_records_subdomain, \
            record_line = u'境内'.encode("utf8"), \
            value = ipv4_address, domain_id = domain_id, \
            login_token = dnspod_conf.login_token, lang="en")
        api_response_data = api()
        print("Domain \"%s.%s\" DDNS update status:%s" % (aim_records_subdomain, \
                records_domain, api_response_data.get("status")["message"]) )
        print("DDNS update finished.")
        return api_response_data
        # print("RecordCreate")
        # api = apicn.RecordCreate("test-www", "A", u'默认'.encode("utf8"), '1.1.1.1', 600, domain_id=domain_id, login_token=login_token)
        # record = api().get("record", {})
        # record_id = record.get("id")
        # print("Record id", record_id)
    def judge_simple_ddns_result(self, request_result, ipv4_address):
        status_code_in_bool = (int(request_result.get("status")["code"]) == 1)
        if request_result == None or not status_code_in_bool:
            return False
        if str(request_result["record"]["value"]) == str(ipv4_address):
            return True
        return False        

if __name__ == '__main__':
    aim_ip_address = "1.1.1.62"
    ddns_tools = dnspod_simple_ddns()
    res = ddns_tools.simple_update_ddns_domain_records(ipv4_address=aim_ip_address)
    print(res)
    print(ddns_tools.judge_simple_ddns_result(request_result=res, ipv4_address=aim_ip_address))
