# coding:utf8
import json

# source code URL: https://blog.csdn.net/weixin_42744102/article/details/99722187
class Find(object):
    def __init__(self, obj):
        self.json_object = None
        if isinstance(obj, str):
            self.json_object = json.loads(obj)
        elif obj != None:
            self.json_object = obj
        else:
            raise TypeError("Unexpected object type.Can not read from NoneType")

    def get_dict_contain_key(self, key):
        result_list = []
        self.__search_dict_contain_key(
            json_object=self.json_object,
            old_path="", 
            key=key, 
            result_list=result_list)
        return result_list

    def __search_dict_contain_key(self, json_object, old_path:str, key, result_list:list):
        if isinstance(json_object, dict):
            for k,v in json_object.items():
                if k == key:
                    result_list.append(
                        {
                            "path": old_path,
                            "obj": json_object
                        }
                    )
                if isinstance(v, dict) or isinstance(v, list):
                    self.__search_dict_contain_key(
                        json_object=v, 
                        old_path= old_path + "[\'{}\']".format(k), 
                        key=key, 
                        result_list=result_list
                    )
        elif isinstance(json_object, list):
            tmp_index = 0
            for item in json_object:
                if isinstance(item, dict) or isinstance(item, list):
                    self.__search_dict_contain_key(
                        json_object=item, 
                        old_path= old_path + "[{}]".format(tmp_index), 
                        key=key, 
                        result_list=result_list
                    )
                tmp_index += 1
        return None

    def get_dict_contain_value(self, key):
        result_list = []
        self.__search_dict_contain_value(
            json_object=self.json_object,
            old_path="", 
            value=key, 
            result_list=result_list)
        return result_list

    def __search_dict_contain_value(self, json_object, old_path:str, value, result_list:list):
        if isinstance(json_object, dict):
            for k,v in json_object.items():
                if v == value:
                    result_list.append(
                        {
                            "path": old_path,
                            "obj": json_object
                        }
                    )
                if isinstance(v, dict) or isinstance(v, list):
                    self.__search_dict_contain_value(
                        json_object=v, 
                        old_path= old_path + "[\'{}\']".format(k), 
                        value=value, 
                        result_list=result_list
                    )
        elif isinstance(json_object, list):
            tmp_index = 0
            for item in json_object:
                if isinstance(item, dict) or isinstance(item, list):
                    self.__search_dict_contain_value(
                        json_object=item, 
                        old_path= old_path + "[{}]".format(tmp_index), 
                        value=value, 
                        result_list=result_list
                    )
                tmp_index += 1
        return None
