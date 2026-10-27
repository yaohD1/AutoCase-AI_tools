*** Settings ***
Resource    ../页面公共关键字.resource

*** Keywords ***
告警-查询当前告警
    [Arguments]    ${级别}=严重    ${时间范围}=最近1小时
    [Documentation]    打开告警管理，按级别和时间范围过滤当前告警列表
    点击元素    //span[text()="告警管理"]
    点击元素    //span[text()="${级别}"]
    点击元素    //button[text()="查询"]

告警-确认告警
    [Arguments]    ${告警ID}
    [Documentation]    对指定告警执行确认操作
    点击元素    //tr[@data-id="${告警ID}"]//button[text()="确认"]
    点击元素    (//*[contains(text(), "确定")])[last()]

告警-统计面板断言
    [Arguments]    ${级别}    ${期望数量}
    [Documentation]    断言顶部统计卡片上某级别告警的数量
    断言元素可见    //div[@class="alarm-card" and contains(text(),"${级别}")]
