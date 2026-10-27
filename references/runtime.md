# 淘宝/天猫：浏览器执行与恢复

## 已验证环境
Windows：Python 3.12、Node、WebCLI Browser Bridge在用户已有浏览器中。collect_reviews.py以Python subprocess argv直接调用WebCLI Node入口，**不经cmd拼JavaScript**，避免中文/箭头/URL & 被shell破坏。默认由PATH上的webcli shim目录解析 node_modules/@jd/webcli/dist/src/main.js；其他布局用--webcli-entry。必要时看已安装browser-task-router和o2官方说明，不卸载/重装Bridge，不修改全局包。

每次eval用IIFE避免持久词法变量冲突，业务响应JSON.stringify保留字符串。单页fetch timeout12s、子进程观察40s；最长等待不会自动重派未知操作。脚本不持久化ambient env；child继承只为运行。

## 页数据
商品页g_bizdata.itemPvItem不总存在（上一版插件依赖它失败）。已有时校验itemId；否则调用页面已加载lib.mtop.request的mtop.taobao.pcdetail.data.get 1.0，使用与页面正常请求一致的detail_v、queryParams/domain/path_name/pcSource。不设置错误type=originaljson/dataType=json（实测会产生UNEXCEPT_REQUEST）。全部ret标志必须SUCCESS，数据itemId必须匹配。

详情seller.userId可能省略，仅encryptUid。**不解密或猜ID**：feedRateList.htm只给auctionNumId即可返回该商品评价，这是指定样本实际验证；有明确userId时可以附带。每条comment.auction.aucNumId必须匹配，以响应源事实绑定目标。

评价页以Accept JSON和X-Requested-With正常同源fetch携带现有会话。普通导航页面可能显示淘宝壳HTML，不等于该JSON接口无数据。body不是JSON/合法包装就报错，不剥HTML伪造评论。

## 保存与复用
输出session.json只记录目标/筛选/实际tab身份；raw/page-NNNN.json保留原响应与digest；progress.json含已取得行；reviews.json为最终数据；Excel旁coverage.json记录范围。输出/参考XLSX均不可覆盖原输入；新目标/筛选用新目录。

当前完整验证：2026-10-27 Asia/Shanghai，淘宝841836517132的带图分页20+9原始条，29初评+16追评=45行；匹配用户导出参考内容与行序（URL绝对化等显示差异须解释）。此证据不保证其他商品、全部筛选或未来网站版本。保留异常理由，而不在连接故障时宣称skill已完成采集。

## 清理
脚本仅清理自己创建且仍能确认的标签页；外部给定--product-tab/--rate-tab由用户拥有。不在身份/连接未知时关闭别的页。--keep-tabs保留本任务页用于诊断，session记录实际tab ID，不把旧ID当永久有效。原始客户参考数据不打包进skill，assets只有无数据表头模板。

在线恢复会保留failed-attempts里的旧LOGIN/VERIFY/API响应，并在新一次请求同意下单次重取已明确拒绝的只读页；不会在同次调用自动反复认证重试。离线模式拒绝失败页，格式/identity/digest损坏不静默替换。

表格写出优先本机捆绑artifact-tool（Python生成已验证五列模型，Node author）；openpyxl作来源/回读验证。没有捆绑引擎的独立环境可用openpyxl写出。实测artifact导出将字面CRLF正规化为LF，因此当原值带CR且须无损保留时采用格式专用string序列化写出；不偷偷修改客户文字。默认实际目标45行已经用artifact引擎回读和视觉校验，五列值/宽一致。
