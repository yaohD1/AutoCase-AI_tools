*** Settings ***
Resource    ../../登录/公共的前置后置.resource
Resource    ../../页面对象层/其他模块/告警管理页面操作.resource

Suite Setup    打开浏览器登录网管2-盲拖
Suite Teardown    关闭浏览器

*** Variables ***
${url}    https://192.168.123.13

*** Test Cases ***
告警查询与确认-编号5120
    [Tags]    dyh
    告警-查询当前告警    级别=严重    时间范围=最近24小时
    告警-统计面板断言    级别=严重    ${期望数量}=3
