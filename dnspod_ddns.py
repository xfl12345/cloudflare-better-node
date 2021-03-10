# -*- coding:utf-8 -*-


import json
import dnspod_apicn as apicn

import conf.dnspod_ddns_conf as dnspod_conf

"""

The constant follow is necessary to be set correctly in file "conf/dnspod_ddns_conf.py":
# This is a example.
login_id = "1234567"
login_token = login_id + "," + "s5a1d6a5s1d56s1ad6a1sd5asd1"
domain = "example@example.com"
aim_subdomain = "diy_a_subdomain"

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
        return (int(api_response_data.get("status")["code"]) == 1)
        # print("RecordCreate")
        # api = apicn.RecordCreate("test-www", "A", u'默认'.encode("utf8"), '1.1.1.1', 600, domain_id=domain_id, login_token=login_token)
        # record = api().get("record", {})
        # record_id = record.get("id")
        # print("Record id", record_id)

if __name__ == '__main__':
    dnspod_simple_ddns().simple_update_ddns_domain_records(ipv4_address="172.64.100.14")
