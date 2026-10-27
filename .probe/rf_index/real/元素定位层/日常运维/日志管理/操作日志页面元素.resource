*** Settings ***
Documentation    操作日志页面元素定位层（脱敏样本，仅补齐被引用的变量名）

*** Variables ***
${操作日志三级菜单}    xpath=//span[text()='操作日志']
${操作日志二级菜单}    xpath=//span[text()='日志管理']
${操作日志列表的来源IP}    xpath=//div[@class='log-table']//tr[1]/td[3]
${操作日志列表的用户名称}    xpath=//div[@class='log-table']//tr[1]/td[2]
${操作日志列表的操作}    xpath=//div[@class='log-table']//tr[1]/td[4]
${操作日志列表的操作对象类别}    xpath=//div[@class='log-table']//tr[1]/td[5]
${操作日志列表的操作结果}    xpath=//div[@class='log-table']//tr[1]/td[6]
${操作日志列表的操作时间}    xpath=//div[@class='log-table']//tr[1]/td[7]
${操作日志列表的用户名称2}    xpath=//div[@class='log-table']//tr[2]/td[2]
${操作日志列表的来源IP2}    xpath=//div[@class='log-table']//tr[2]/td[3]
${操作日志列表的操作2}    xpath=//div[@class='log-table']//tr[2]/td[4]
${操作日志列表的操作对象类别2}    xpath=//div[@class='log-table']//tr[2]/td[5]
${操作日志列表的操作结果2}    xpath=//div[@class='log-table']//tr[2]/td[6]
${操作日志列表的操作时间2}    xpath=//div[@class='log-table']//tr[2]/td[7]
${共x条}    xpath=//span[contains(text(),'共')]
${操作日志列表的详情图标}    xpath=//div[@class='log-table']//tr[1]//i[@class='detail']
${操作日志详情的接口url}    xpath=//div[@class='detail-panel']//label[text()='接口URL']/following-sibling::span
${操作日志详情的业务错误码}    xpath=//div[@class='detail-panel']//label[text()='业务错误码']/following-sibling::span
${操作日志详情的请求内容}    xpath=//div[@class='detail-panel']//pre[@class='request-body']
${操作日志详情的响应内容}    xpath=//div[@class='detail-panel']//pre[@class='response-body']
