*** Settings ***
Documentation    左侧菜单元素定位层（脱敏样本）

*** Variables ***
${左侧菜单}    xpath=//ul[@class='left-menu']
${网元管理二级菜单}    xpath=//span[text()='网元管理']
${网元列表三级菜单}    xpath=//span[text()='网元列表']
${告警管理二级菜单}    xpath=//span[text()='告警管理']
