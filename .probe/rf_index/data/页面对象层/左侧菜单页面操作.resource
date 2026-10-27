*** Settings ***
Resource    页面公共关键字.resource

*** Keywords ***
进入网元列表二级菜单
    [Documentation]    左侧菜单：日常运维 -> 网元列表
    点击元素    //span[text()="日常运维"]
    点击元素    //span[text()="网元列表"]
    等待页面加载完成

进入日志管理二级菜单
    [Documentation]    左侧菜单：日常运维 -> 日志管理
    点击元素    //span[text()="日常运维"]
    点击元素    //span[text()="日志管理"]
    等待页面加载完成

进入操作日志三级菜单
    [Documentation]    日志管理页签：操作日志
    点击元素    //div[contains(@class,"tab") and contains(text(),"操作日志")]
    等待页面加载完成

进入SIM卡信息菜单
    [Documentation]    左侧菜单：网络设置 -> 移动网络 -> SIM卡信息
    点击元素    //span[text()="网络设置"]
    点击元素    //span[text()="移动网络"]
    点击元素    //span[text()="SIM卡信息"]
    等待页面加载完成
