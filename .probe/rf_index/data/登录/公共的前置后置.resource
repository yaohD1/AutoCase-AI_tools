*** Settings ***
Resource    ../页面对象层/页面公共关键字.resource

*** Variables ***
${email}    admin@ailink.com
${passwd}    admin

*** Keywords ***
打开谷歌浏览器并访问登录页面
    [Documentation]    拉起 Chrome 并打开网管登录页，等待静态资源加载完毕
    打开浏览器    ${url}    chrome
    等待页面加载完成

登录
    [Arguments]    ${账号}=${email}    ${密码}=${passwd}
    [Documentation]    在登录页输入账号密码并点击确定
    输入文本    (//input)[1]    ${账号}
    输入文本    (//input)[2]    ${密码}
    点击元素    (//*[contains(text(), "确定")])[2]

拖动完成拼图
    [Documentation]    盲拖方案：固定偏移多次拖动滑块，已人工验证可行；验证码识别不稳定时不依赖图像识别
    拖动滑块    120
    拖动滑块    60
    等待页面加载完成

打开浏览器登录网管2-盲拖
    [Documentation]    完整登录前置：打开浏览器 -> 输账号密码 -> 拖滑块过验证码
    打开谷歌浏览器并访问登录页面
    登录
    拖动完成拼图
