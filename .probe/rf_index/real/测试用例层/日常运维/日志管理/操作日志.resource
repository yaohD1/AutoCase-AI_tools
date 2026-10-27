*** Settings ***
Library    SeleniumLibrary    timeout=10    implicit_wait=10    #screenshot_root_directory=./screenshots/
Library    MyTestLibrary
Library    String
Resource    ../../../元素定位层/日常运维/日志管理/操作日志页面元素.resource
Resource    ../../../元素定位层/左侧菜单页面元素.resource
Variables    ../../../测试数据层/公共数据.py


*** Keywords ***
进入操作日志三级菜单
    Click Element    ${操作日志三级菜单}
    Sleep    2        

# 进入安全日志三级菜单
    # Click Element    ${安全日志三级菜单}
    # Sleep    2   

# 用于权限断言
操作日志三级菜单不可见    
    Page Should Not Contain Element    ${操作日志三级菜单}    
    Sleep    1 

 
操作日志列表加载完成
    Wait Until Element Contains    ${操作日志列表的来源IP}    ${hostIp}    timeout=60   
    

操作日志列表断言
    [Arguments]
    ...    ${用户名称}=admin@ailink.com	
    ...    ${来源IP}=${hostIp}
    ...    ${操作}=
    ...    ${操作对象类别}=
    ...    ${操作结果}=
    ...    ${操作时间}=
    ...    ${第几条数据}=1        
    操作日志列表加载完成
    ${操作时间减少一分钟}=    Date Dec One Min    ${操作时间}
    ${操作时间增加一分钟}=    Date Add One Min    ${操作时间}        
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的用户名称}    ${用户名称}       
    # Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的来源IP}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作}    ${操作}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作对象类别}    ${操作对象类别}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作结果}    ${操作结果} 
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间}        
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间增加一分钟}
                        

    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的用户名称2}    ${用户名称}       
    # Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的来源IP2}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作2}    ${操作}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作对象类别2}    ${操作对象类别}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作结果2}    ${操作结果}                       
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间}     
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间增加一分钟}              
    Sleep    2  
    


操作日志列表数据条数
    ${value0}=    Get text    ${共x条}    
    ${num}=    Get Substring    ${value0}    2    -2    
    Return From Keyword    ${num}
    


操作日志详情
    [Arguments]    ${content}=   
    Sleep    1    
    Click Element    ${操作日志列表的详情图标}
    Sleep    1    
    Element Should Contain    ${操作日志详情的接口url}    /configField        
    Element Should Contain    ${操作日志详情的业务错误码}    200
        
    Element Should Contain    ${操作日志详情的请求内容}    "object":"${content}"
    # Element Should Contain    ${操作日志详情的响应内容}    "msg":"修改网元配置成功"
    

IMS操作日志列表断言
    [Arguments]
    ...    ${用户名称}=admin@ailink.com	
    ...    ${来源IP}=${hostIpbj}
    ...    ${操作}=
    ...    ${操作对象类别}=
    ...    ${操作结果}=
    ...    ${操作时间}=
    ...    ${第几条数据}=1        
    #操作日志列表加载完成
    ${操作时间减少一分钟}=    Date Dec One Min    ${操作时间}
    ${操作时间增加一分钟}=    Date Add One Min    ${操作时间}        
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的用户名称}    ${用户名称}       
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的来源IP}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作}    ${操作}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作对象类别}    ${操作对象类别}
    #Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作结果}    ${操作结果} 
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间}        
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${操作日志列表的操作时间}    ${操作时间增加一分钟}
                        

    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的用户名称2}    ${用户名称}       
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的来源IP2}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作2}    ${操作}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作对象类别2}    ${操作对象类别}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作结果2}    ${操作结果}                       
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间}     
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${操作日志列表的操作时间2}    ${操作时间增加一分钟}              
    Sleep    2 
