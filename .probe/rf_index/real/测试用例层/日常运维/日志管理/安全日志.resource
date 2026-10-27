*** Settings ***
Library    SeleniumLibrary    timeout=10    implicit_wait=10    #screenshot_root_directory=./screenshots/
Library    String
Library    DateTime
Library    MyTestLibrary
Resource    ../../../元素定位层/日常运维/日志管理/安全日志页面元素.resource
Resource    ../../../元素定位层/左侧菜单页面元素.resource
Variables    ../../../测试数据层/公共数据.py


*** Keywords ***
进入安全日志三级菜单
    Click Element    ${安全日志三级菜单}    
    Sleep  1

# 用于权限断言
安全日志三级菜单可见    
    Page Should Contain Element    ${安全日志三级菜单2}    
    Sleep    1
    

# 用于权限断言
安全日志列表用户名称可见
    Page Should Contain Element    ${安全日志列表用户名称断言}
    Sleep    1        
      

安全日志列表断言
    [Arguments]
    ...    ${用户名称}=admin@ailink.com	
    ...    ${来源IP}=${hostIp}
    ...    ${操作}=
    ...    ${操作对象类别}=
    ...    ${操作结果}=
    ...    ${操作时间}=
    ...    ${时长}=
    ...    ${第几条数据}=1        
    ${操作时间减少一分钟}=    Date Dec One Min    ${操作时间}
    ${操作时间增加一分钟}=    Date Add One Min    ${操作时间}        
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的用户名称}    ${用户名称}       
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的来源IP}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作}    ${操作}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作对象类别}    ${操作对象类别}
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作结果}    ${操作结果} 
    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的时长}    ${时长} 
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作时间}    ${操作时间}         
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作时间}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作时间}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作时间}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='1'    Element Should Contain    ${安全日志列表的操作时间}    ${操作时间增加一分钟}


    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的用户名称2}    ${用户名称}       
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的来源IP2}    ${来源IP}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作2}    ${操作}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作对象类别2}    ${操作对象类别}
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作结果2}    ${操作结果} 
    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的时长2}    ${时长}                       
    ${status}    ${value}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作时间2}    ${操作时间}   
    ${status2}    ${value2}=    Run Keyword And Ignore Error    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作时间2}    ${操作时间减少一分钟}            
    Run Keyword If    '${status}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作时间2}    ${操作时间}         
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='PASS'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作时间2}    ${操作时间减少一分钟}                  
    Run Keyword If    '${status}'=='FAIL' and '${status2}'=='FAIL'    Run Keyword If    '${第几条数据}'=='2'    Element Should Contain    ${安全日志列表的操作时间2}    ${操作时间增加一分钟}            
    Sleep    2  
    

安全日志列表加载完成
    Wait Until Element Contains    ${安全日志列表的来源IP}    ${hostIp}    timeout=20


安全日志列表数据条数
    ${value0}=    Get text    ${共x条}    
    ${num}=    Get Substring    ${value0}    2    -2    
    Return From Keyword    ${num}  
        
