# Reddit 股票新闻与情绪采集

该脚本通过 Reddit 官方 OAuth Data API 搜索股票相关社区，导出帖子元数据、链接、股票代码和基础词典情绪分数。它不会绕过登录、访问控制或速率限制。

## 配置

先在 Reddit 申请并获批 Data API 访问，创建 OAuth 应用，然后安装依赖：

```bash
python -m pip install -r requirements.txt
export REDDIT_CLIENT_ID='...'
export REDDIT_CLIENT_SECRET='...'
export REDDIT_USER_AGENT='linux:reddit-market-sentiment:v1.0.0 (by /u/你的用户名)'
```

不要提交真实密钥。User-Agent 应按 Reddit 要求包含平台、应用 ID、版本和联系人。

## 使用

```bash
python reddit_market_sentiment.py \
  --subreddits stocks,investing,wallstreetbets \
  --query 'AAPL OR TSLA OR NVDA OR earnings' \
  --tickers AAPL,TSLA,NVDA \
  --time week --limit 500 \
  --format jsonl --output data/reddit_market.jsonl
```

CSV 输出：

```bash
python reddit_market_sentiment.py --format csv --output data/reddit_market.csv
```

情绪分数是透明的英文关键词基线（-1 到 1），适合管道验证，不应直接作为投资依据。生产分析建议在合规前提下替换为经过金融文本验证的模型，并保留模型版本与采集时间。

脚本会响应 `429`，读取 Reddit 的 `X-Ratelimit-Remaining` 和 `X-Ratelimit-Reset`，并自动退避。Reddit 要求删除已被用户删除的内容；若长期存储，应实现定期复查/清理流程。
