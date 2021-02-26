# requirements = ["pandas", "matplotlib", "seaborn"]
def check_requirement(package):
    import os
    try:
        exec("import {0}".format(package))
    except ModuleNotFoundError:
        inquiry = input("This script requires {0}. Do you want to install {0}? [y/n]".format(package))
        while (inquiry != "y") and (inquiry != "n"):
            inquiry = input("This script requires {0}. Do you want to install {0}? [y/n]".format(package))
        if inquiry == "y":
            print("Execute commands: pip install {0}".format(package))
            os.system("pip install {0}".format(package))
        else:
            print("{0} is missing, so the program exits!".format(package))
            exit(-1)

def check_module_in_list(requirements:list):
    for requirement in requirements:
        check_requirement(requirement)
