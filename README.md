# 评论采集

多平台评价采集 Skill，调用名 `$review-collect`。GitHub仓库：`ctzlyj/review-collect-skill`。

| 平台 | 当前范围 |
|---|---|
| 淘宝/天猫 | 已实现商品评价/初追评、媒体URL、SKU与Excel |
| PDD | 已迁入商家后台月窗/分页、商品与评分筛选、媒体URL及可选下载；当前未登录，真实店铺采集待验收 |
| 京东/1688/抖音 | 后续扩展，尚未实现 |

## 使用

把仓库根目录安装为个人技能 `review-collect`，入口 `SKILL.md`。依赖既有Node/WebCLI Browser Bridge、Python3.12/openpyxl；PDD图片下载额外用已安装Pillow。没有自动软件安装、授权写操作或商业平台提交。

淘宝：`python -X utf8 scripts/collect.py --url "商品链接" --output-dir "仓库外目录"`。不要视频时加 `--exclude-videos`，保留五列、全部已选评论与源证据，只让视频列为空。

PDD：正常登录当前店铺评价页，`python -X utf8 scripts/collect.py --platform pdd --session SESSION --tab TARGET --prepare`，然后正常查询一次，再传 `--mall-id CURRENT_MALL --output-dir DIR --months 1 --limit 100` 采集。`--include-videos --download-media` 显式选用；具体边界见 [PDD运行说明](references/pdd.md)。

统一Excel：sheet“全部”，五列 **内容、图片、视频、SKU、评论时间**。图片/视频用URL逗号连接；源身份与范围保存JSON，不增加用户未要求列。

## 管理与验证

`python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v`；Skill frontmatter可用本机skill-creator quick_validate检验。

来源PDD模块固定commit与adaptation记录见 [来源记录](references/source-provenance.json)。只迁入只读采集，不携带JD生成/评价提交/授权核心；不继承五星和正面文本隐性过滤、默认店铺ID或伪装/拒绝自动刷新逻辑。

所有输出在repo外；公开仓库只存技能源码/测试/空白模板。真实评论、媒体、请求响应、cookies/anti-content、会话与浏览器配置均不得提交。平台拒绝/限流/登录立即停，不绕过；已达到limit不等于全部范围完整。

原淘宝采集脚本命令兼容保留。2026-10-27迁入测试/实时验证边界见PDD说明；之前目标商品验证不保证其他平台或未来接口。
