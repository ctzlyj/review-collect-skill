# 拼多多后台评价采集

来源：ctzlyj/jd-jx-pictured-review 的 platforms/pinduoduo 子模块，固定commit c7ca61f3ce97019a1b85ebc244a75840e6741095；文件哈希和保留/排除点见 source-provenance.json。只迁入读评价，不引入JD图片生成、提交/成员授权核心。

## 正常会话

mms.pinduoduo.com商家页面自己调用 POST /saturn/reviews/list，result.data为评价列表、totalNum为当前查询范围总数。默认pageSize10，月度UTC+08窗口，包含月初至月末最后一秒/当前时间。本地最多24个月、每窗200页和20000选中行，窗口完整性由已读取分页推导，不把源“最多2000条”的经验当平台永久规格。

在既有WebCLI session的真实商家tab上--prepare，再页面点击查询。模块仅观察该同源read endpoint的fetch/XHR，不读取document.cookie，也不导出请求headers。anti-content/body保留在页面内存；只返回captureId和已观测的mallId（如果有）及有限查询条件摘要。

--mall-id为用户明确选择的店铺。如后台body不提供mallId，结果明确标 caller-selected-not-observed；这不是session证明，使用者需核对当前商家显示。不允许默填原样本店铺。换页/查询条件后captureId变更，旧任务停止不挪用新会话。

每个实际请求只用同页既有合法参数，覆盖调用者所选日期/page/goods字段，不改变其他查询条件。生成/伪造anti-content、复制Cookie到脚本、无头浏览器隐藏自动化参数和拒绝后自动刷新均不迁入。错误立即返回，需用户正常登录或处理验证。

## 输出

reviews.json统一rows仍是原始评价事实；评分/店铺/goodsId/sourceReviewId/页面/窗口均在source保留。中文规格spec_key/spec_value归一化一次，空日期未知；图片保留源URL，video和livePhotoVideo需--include-videos。negative/star筛选不继承来源JD用途，只有显式--stars筛分。

拼多多_评价.xlsx单sheet全部、五列内容/图片/视频/SKU/评论时间。checkpoint.json/raw和summary.json在用户输出目录；source身份/digest检查防误复用。--from-raw使用既存选择信息，不用新当前日期窗口伪造续跑。

--download-media可选；默认4并发最多8、单媒体30MB，私有下载不携带后台Cookie，域限定pddpic/yangkeduo；正常其他域不猜授权。校验实际图片/视频容器签名，hash去重会保持每行媒体关系，不因全媒体重复或失败删评论；media-manifest.json显式记录 saved/reused/failed，不把少文件当全成功。

## 验证边界

2026-10-27当前可达PDD后台页跳转到login，因此只确认登录拒绝路径，**未执行真实店铺评价拉取或批量媒体下载**。原来源1000条基线不升级为迁入验证。本仓库合成分页/字段/下载测试以及端到端离线Excel验证分别标注。未来用户正常登录并选择店铺后可按以上流程验收，不能以单个来源测试替代。

响应桥只传原始文本给Python JSON解析，不在浏览器将业务JSON转成Number，避免大整数评价ID失真；digest和去重都以此拥有边界为准。RATE_LIMIT与已明确认证拒绝只允许下次明确恢复单次重取，保留旧拒绝记录，不在一次执行中自动重试。
