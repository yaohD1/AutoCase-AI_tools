*** Settings ***
Documentation    安全日志页面元素定位层（脱敏样本，仅补齐被引用的变量名）

*** Variables ***
${安全日志三级菜单}    xpath=//span[text()='安全日志']
${安全日志三级菜单2}    xpath=//span[text()='安全日志']
${安全日志列表用户名称断言}    xpath=//div[@class='sec-table']//tr[1]/td[2]
${安全日志列表的用户名称}    xpath=//div[@class='sec-table']//tr[1]/td[2]
${安全日志列表的来源IP}    xpath=//div[@class='sec-table']//tr[1]/td[3]
${安全日志列表的操作}    xpath=//div[@class='sec-table']//tr[1]/td[4]
${安全日志列表的操作对象类别}    xpath=//div[@class='sec-table']//tr[1]/td[5]
${安全日志列表的操作结果}    xpath=//div[@class='sec-table']//tr[1]/td[6]
${安全日志列表的时长}    xpath=//div[@class='sec-table']//tr[1]/td[7]
${安全日志列表的操作时间}    xpath=//div[@class='sec-table']//tr[1]/td[8]
${安全日志列表的用户名称2}    xpath=//div[@class='sec-table']//tr[2]/td[2]
${安全日志列表的来源IP2}    xpath=//div[@class='sec-table']//tr[2]/td[3]
${安全日志列表的操作2}    xpath=//div[@class='sec-table']//tr[2]/td[4]
${安全日志列表的操作对象类别2}    xpath=//div[@class='sec-table']//tr[2]/td[5]
${安全日志列表的操作结果2}    xpath=//div[@class='sec-table']//tr[2]/td[6]
${安全日志列表的时长2}    xpath=//div[@class='sec-table']//tr[2]/td[7]
${安全日志列表的操作时间2}    xpath=//div[@class='sec-table']//tr[2]/td[8]
