from urllib import parse
import requests
import json


import my_const
import json_tools
from forced_ip_https_adapter import ForcedIPHTTPSAdapter


"""
A normal Cloudflare API requests:

curl -X GET "https://api.cloudflare.com/client/v4/zones/cd7d0123e3012345da9420df9514dad0" \
     -H "Content-Type:application/json" \
     -H "X-Auth-Key:1234567893feefc5f0q5000bfo0c38d90bbeb" \
     -H "X-Auth-Email:example@example.com"

The constant follow is necessary to be set correctly in file "conf/cloudflare_cdn_ddns_conf.py":
# This is a example.
zone_id="cd7d0123e3012345da9420df9514dad0"
x_auth_email="example@example.com"
x_auth_key="1234567893feefc5f0q5000bfo0c38d90bbeb"
aim_subdomain = "diy_a_subdomain"

Q: What is "x_auth_key"? How to get "x_auth_key"?
A: Well.Login to Cloudflare.Then open the URL: https://dash.cloudflare.com/profile/api-tokens
You will find it - "Global API Key"
That is.

Q: How to get "zone_id"?
A: When you enter a domain dashboard on the Cloudflare, you can see the "Overview" at first.
Just slide down the page, you may find the "Zone ID" on the right.

"""
import conf.cloudflare_cdn_ddns_conf as cf_conf

class cf_simple_ddns:
    def __init__(self) -> None:
        self.user_agent = my_const.USER_AGENT

        # user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.150 Safari/537.36 Edg/88.0.705.63"

        self.specific_ip_address = "1.1.1.0"
        self.cf_zones_url = "https://api.cloudflare.com/client/v4/zones"
        self.cf_zones_dns_records_url = self.cf_zones_url + "/" + cf_conf.zone_id + "/dns_records"
        self.second_level_domain = None
        self.ddns_domain = None
        self.ddns_domain_id = self.get_ddns_domain_id()

    def get_cf_api_necessary_headers(self, url):
        url_parse = parse.urlparse(url=url)
        hostname = url_parse.hostname
        headers={"host": hostname, "User-Agent": self.user_agent }
        headers[my_const.HEADER_FIELD_NAMES.X_AUTH_EMAIL] = cf_conf.x_auth_email
        headers[my_const.HEADER_FIELD_NAMES.X_AUTH_KEY] = cf_conf.x_auth_key
        headers[my_const.HEADER_FIELD_NAMES.CONTENT_TYPE] = "application/json"
        return headers

    def try_text2json(self, json_str:str):
        json_obj = None
        if json_str:
            try:
                json_obj = json.loads(s=json_str)
            except Exception:
                pass
        return json_obj

    def get_all_dns_records(self)->str:
        url = self.cf_zones_dns_records_url + "?" + "match=all"
        headers = self.get_cf_api_necessary_headers(url=url)
        res = self.forced_ip_request(url=url, headers=headers).text
        return self.try_text2json(json_str=res)

    def get_type_a_dns_records(self):
        url = self.cf_zones_dns_records_url + "?" + "type=A" + "&" + "match=all"
        headers = self.get_cf_api_necessary_headers(url=url)
        res = self.forced_ip_request(url=url, headers=headers).text
        return self.try_text2json(json_str=res)

    def forced_ip_request(self, url:str, headers, method:str="get", payload=None)->requests.Response:
        session = requests.Session()
        session.mount(prefix="https://" , 
                        adapter=ForcedIPHTTPSAdapter(
                            max_retries=3, 
                            dest_ip=self.specific_ip_address ))
        if method == "get":
            response = session.get(url=url, verify=True, headers=headers, stream=False)
        elif method == "put":
            response = session.put(url=url, verify=True, headers=headers, data=payload)
        else:
            response = session.post(url=url, verify=True, headers=headers, stream=False)
        return response

    def get_zones_details(self):
        url = self.cf_zones_url
        headers = self.get_cf_api_necessary_headers(url=url)
        res = self.forced_ip_request(url=url, headers=headers).text
        return self.try_text2json(json_str=res)

    def get_ddns_domain_id(self):
        def is_request_failed(request_result):
            if request_result == None or not bool(request_result["success"]):
                return True
        if self.ddns_domain == None:
            if self.second_level_domain == None:
                request_result = self.get_zones_details()
                if is_request_failed(request_result):
                    return None
                search_tool = json_tools.Find(request_result)
                search_result = search_tool.get_dict_contain_value(cf_conf.zone_id)
                self.second_level_domain = str(search_result[0]["obj"]["name"])
            self.ddns_domain = cf_conf.aim_subdomain + "." + self.second_level_domain
        request_result = self.get_all_dns_records()
        if is_request_failed(request_result):
            return None
        search_tool = json_tools.Find(request_result)
        search_result = search_tool.get_dict_contain_value(self.ddns_domain)
        if search_result:
            return str(search_result[0]["obj"]["id"])
        else:
            return None
    
    def get_aim_subdomain_details(self):
        def is_request_failed(request_result):
            if request_result == None or not bool(request_result["success"]):
                return True
        if self.ddns_domain_id == None:
            if self.ddns_domain == None:
                if self.second_level_domain == None:
                    request_result = self.get_zones_details()
                    if is_request_failed(request_result):
                        return None
                    search_tool = json_tools.Find(request_result)
                    search_result = search_tool.get_dict_contain_value(cf_conf.zone_id)
                    self.second_level_domain = str(search_result[0]["obj"]["name"])
                self.ddns_domain = cf_conf.aim_subdomain + "." + self.second_level_domain
            request_result = self.get_all_dns_records()
            if is_request_failed(request_result):
                return None
            search_tool = json_tools.Find(request_result)
            search_result = search_tool.get_dict_contain_value(self.ddns_domain)
            if search_result:
                self.ddns_domain_id = str(search_result[0]["obj"]["id"])
        else:
            url = self.cf_zones_dns_records_url + "/" + self.ddns_domain_id
            headers = self.get_cf_api_necessary_headers(url=url)
            res = self.forced_ip_request(url=url, headers=headers).text
            request_result = self.try_text2json(json_str=res)
            if is_request_failed(request_result):
                return None
        search_tool = json_tools.Find(request_result)
        search_result = search_tool.get_dict_contain_value(self.ddns_domain)
        if search_result :
            return search_result[0]["obj"]
        else:
            return None
        
    def simple_update_ddns_domain_records(self, ipv4_address:str):
        if self.ddns_domain_id == None:
            self.ddns_domain_id = self.get_ddns_domain_id()
            if self.ddns_domain_id == None:
                return False
        url = self.cf_zones_dns_records_url + "/" + self.ddns_domain_id
        headers = self.get_cf_api_necessary_headers(url=url)
        param = {
            "type":"A",
            "name":cf_conf.aim_subdomain,
            "content":ipv4_address,
            "ttl":120,
            "proxied":False
        }
        payload = json.dumps(param)
        res = self.forced_ip_request(url=url, headers=headers, method="put", payload=payload).text
        request_result = self.try_text2json(json_str=res)
        # print(request_result)
        if request_result == None or not bool(request_result["success"]):
            return False
        if str(request_result["result"]["content"]) == str(ipv4_address):
            return True
        return False


if __name__ == "__main__":
    test = cf_simple_ddns()
    print(test.ddns_domain_id)
    print(test.simple_update_ddns_domain_records(ipv4_address="172.64.100.14"))





