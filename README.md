# 上海天空轨道场

一个“站在上海向上看”的卫星过境科普网页。首屏是四弧天空窗口与全天时间轴；下方包含今日精选、尺度科普、小时/类别图表、可检索过境表和 CSV 导出。

## DEMO 演示地址
https://jeffreylexxx.github.io/shanghai-satellite-dashboard-v2/

## 更新流程

```text
CelesTrak ACTIVE GP/OMM CSV
        ↓ 一次下载 + 完整性检查
SGP4 / 上海站心方位角与仰角 / 5 分钟采样
        ↓
latest.json + 90 天轻量历史 + 可审计原始快照 artifact
        ↓ 校验通过才继续
单文件离线 HTML → GitHub Pages
```

- 数据源：CelesTrak `GROUP=ACTIVE&FORMAT=CSV`
- 观测点：上海市中心 31.2304°N, 121.4737°E
- 时区：Asia/Shanghai（UTC+8）
- 阈值：仰角 ≥ 10°
- 轨道模型：SGP4
- “全天不同卫星”：NORAD ID 去重
- “连续过境段”：同一目标连续处于阈值以上的一段弧
- 类型和部分运营方：依据公开名称保守推断，网页明确标注

若下载结果过小、有效目标少于 10,000、JSON 统计不一致或页面未正确嵌入数据，工作流会失败并停止部署，因此线上会保留上一次成功版本。

使用本地传统 TLE 做离线种子：

```powershell
python scripts/update_data.py --date 2026-09-12 --input path\to\active.tle
python scripts/build_site.py
python scripts/validate_build.py
```

## 文件说明

- `src/index.template.html`：页面源码，`__SKY_DATA__` 是构建占位符
- `public/index.html`：可直接打开、可部署的最终单文件页面
- `public/data/latest.json`：当天结构化数据
- `scripts/update_data.py`：下载、轨道计算、聚合
- `scripts/build_site.py`：把数据嵌入离线 HTML
- `scripts/validate_build.py`：阻止不完整构建发布
- `.github/workflows/update-and-deploy.yml`：每日更新和 Pages 部署
- `data/history.json`：最多 90 天的轻量日统计

## 数据解释边界

页面是基于公开轨道根数的预测，不是实时雷达观测，也不代表卫星一定肉眼可见。覆盖直径是统一最低仰角下的几何视线估算，不等于通信服务覆盖。Starlink 的 45–280 Mbps 指官方用户端典型下载速率范围，不是单颗卫星的总容量。

## License

页面与脚本使用 MIT License。卫星轨道数据由来源方提供并受其数据政策约束；类别卫星图为本项目概念示意，不对应特定编号实拍。
