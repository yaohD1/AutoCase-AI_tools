*** Settings ***
Resource    ../../页面公共关键字.resource

*** Keywords ***
寻找UP/CP网元
    [Arguments]    ${网元名称}    ${网元IP}
    [Documentation]    在网元列表中按名称和 IP 定位到具体行
    输入文本    //input[@placeholder="请输入网元名称"]    ${网元名称}
    输入文本    //input[@placeholder="请输入网元IP"]    ${网元IP}
    点击元素    //button[text()="查询"]
    等待页面加载完成

配置页面跳转
    [Documentation]    点击当前选中行的配置管理按钮，跳到设备配置页
    点击元素    //button[text()="配置管理"]
    等待页面加载完成

复位操作
    [Documentation]    点击复位按钮并在确认框中确认
    点击元素    //button[text()="复位"]
    点击元素    (//*[contains(text(), "确定")])[last()]

网元列表-新增网元
    [Arguments]    ${网元类型}    ${网元名称}    ${网元IP}    ${网元版本}=test
    [Documentation]    点击新增，选择网元类型，填写名称/IP/版本，区域选择未分区，点击确定
    点击元素    //button[text()="新增"]
    点击元素    //span[text()="${网元类型}"]
    输入文本    //input[@label="网元名称"]    ${网元名称}
    输入文本    //input[@label="网元IP"]    ${网元IP}
    输入文本    //input[@label="网元版本"]    ${网元版本}
    点击元素    //span[text()="未分区"]
    点击元素    (//*[contains(text(), "确定")])[last()]

网元列表-修改网元
    [Arguments]    ${原网元名称}    ${新网元名称}
    [Documentation]    点击目标行的编辑按钮，修改名称后确定
    输入文本    //input[@placeholder="请输入网元名称"]    ${原网元名称}
    点击元素    //button[text()="查询"]
    点击元素    //button[text()="编辑"]
    输入文本    //input[@label="网元名称"]    ${新网元名称}
    点击元素    (//*[contains(text(), "确定")])[last()]

网元列表-删除网元
    [Arguments]    ${网元名称}
    [Documentation]    点击目标行的删除按钮，在确认框中确定
    输入文本    //input[@placeholder="请输入网元名称"]    ${网元名称}
    点击元素    //button[text()="查询"]
    点击元素    //button[text()="删除"]
    点击元素    (//*[contains(text(), "确定")])[last()]

网元列表-断言列表网元
    [Arguments]    ${网元名称}    ${expect}=出现
    [Documentation]    断言列表中包含/不包含指定网元名称
    断言元素可见    //td[contains(text(),"${网元名称}")]    ${expect}

网元列表-拓扑断言
    [Arguments]    ${网元名称}    ${expect}=出现
    [Documentation]    切到拓扑管理页签，断言拓扑图中存在/不存在该网元
    点击元素    //div[text()="拓扑管理"]
    断言元素可见    //div[@class="topo-node" and contains(text(),"${网元名称}")]    ${expect}

网元列表-导出
    [Arguments]    ${文件路径}
    [Documentation]    导出当前过滤后的网元列表为 Excel，文件落在下载目录
    点击元素    //button[text()="导出"]
    点击元素    (//*[contains(text(), "确定")])[last()]
