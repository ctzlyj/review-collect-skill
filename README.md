# 淘宝 / 天猫评价采集 Skill

私有维护仓库，技能调用名：`$taobao-review-collect`。独立实现，不依赖此前的自产 Chrome 采集插件，不是至尊宝官方产品。

## 内容

- `SKILL.md`：技能入口与授权边界。
- `scripts/collect_reviews.py`：复用正常已登录浏览器的 WebCLI 会话，读取评价分页，保留检查点。
- `scripts/export_workbook.py`：生成参考格式 Excel 并回读验证。
- `scripts/artifact_workbook.mjs`：有捆绑表格引擎时的 Node 写出入口。
- `assets/workbook-template.xlsx`：只有表头的空白模板，无客户评价数据。
- `scripts/test_*.py`：人工数据边界测试。

## 使用

把本仓库的技能文件放到个人技能目录下的 `taobao-review-collect` 文件夹，或在 Codex 中显式指定本仓库的 `SKILL.md`。

需要：Python 3.12、openpyxl（已验证版本 3.1.5）、Node，以及已连接且正常登录的 WebCLI Browser Bridge。本仓库不自动安装或升级这些软件，也不保存或复制登录凭据。

```powershell
python -X utf8 scripts/collect_reviews.py --url "https://item.taobao.com/item.htm?id=商品ID" --output-dir "D:/review-outputs/商品ID"
```

默认采集带图评价；可按需求选择 `--filter all|image|video|append`。Excel 工作表为“全部”，列顺序为：**内容、图片、视频、SKU、评论时间**。图片/视频按 URL 导出，不在默认流程偷偷下载媒体文件。初评和追评分行，缺失日期保持空白。

离线重放已保留的完整检查点：

```powershell
python -X utf8 scripts/collect_reviews.py --output-dir "D:/review-outputs/商品ID" --from-raw
```

## 验证

```powershell
python -X utf8 -m unittest discover -s scripts -p "test_*.py" -v
```

2026-10-27 已在指定淘宝商品上验证完整在线采集及独立离线重放。该记录不保证其他商品、未来接口版本或受限内容。具体行为和恢复条件见 `references/runtime.md`。

## 数据与权限

- 本仓库仅包含源码、测试、技能元信息和空白模板。
- 原始评价、导出表格、浏览器会话、网络响应和凭据不上传；请把采集输出放到仓库外。
- 登录、验证码、权限拒绝或限流时停止，不绕过限制，不复制 Cookie，不调用商业插件会员后端。
- 统计与结束状态只针对实际访问的筛选分页，不宣称获取平台隐藏、删除或受限的全部评价。
