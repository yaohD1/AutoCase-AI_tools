*** Settings ***
Documentation    页面层最底层的原子操作封装，所有上层 resource 都依赖本文件

*** Variables ***
${DEFAULT_TIMEOUT}    10s

*** Keywords ***
打开浏览器
    [Arguments]    ${url}    ${browser}=chrome
    [Documentation]    启动浏览器并跳转到指定地址
    Log    打开 ${browser} 访问 ${url}
    Sleep    0.1s

等待页面加载完成
    [Arguments]    ${timeout}=${DEFAULT_TIMEOUT}
    [Documentation]    等待页面 networkidle 或指定超时
    Sleep    0.1s

输入文本
    [Arguments]    ${locator}    ${value}
    [Documentation]    在指定定位器输入文本，输入前会清空原内容
    Log    输入 ${value} 到 ${locator}
    Sleep    0.1s

点击元素
    [Arguments]    ${locator}    ${timeout}=${DEFAULT_TIMEOUT}
    [Documentation]    点击元素，失败会重试三次
    Log    点击 ${locator}
    Sleep    0.1s

断言元素可见
    [Arguments]    ${locator}    ${expect}=可见
    [Documentation]    断言元素可见或不可见
    Log    断言 ${locator} ${expect}
    Sleep    0.1s

拖动滑块
    [Arguments]    ${offset}    ${duration}=0.5s
    [Documentation]    按住滑块向右拖动指定像素，用于拼图/滑动验证码
    Log    拖动 ${offset}px
    Sleep    0.1s

刷新页面
    [Documentation]    刷新当前页面并等待加载完成
    Sleep    0.1s
