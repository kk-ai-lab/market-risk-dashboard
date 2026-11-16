# 金融术语表（Glossary）

**目的：** 统一产品内中英术语翻译，避免同一术语出现多个译法（PRD §8.7 / 架构 §1.9 落地）。
**约定：** 本表术语优先于任何其他翻译；i18n key 文案与 AI 简报均须遵循本表。
**维护：** 新增术语先在此登记，再同步到 `src/i18n/locales/*` 与 AI prompt 模板。

---

## 1. 核心术语

| English | 中文 | 定义 / 产品内用法 |
|---|---|---|
| Risk On | 风险偏好 | 投资者愿意承担风险的市况；通常伴随股市上涨、利差收窄、波动率走低。产品中对应风险等级 `risk_on`。 |
| Risk Off | 风险规避 | 投资者回避风险的市况；通常伴随股市下跌、利差走阔、波动率走高。对应风险等级 `risk_off`。 |
| Market Regime | 市场状态 | 对当前市场环境的定性分类（如 goldilocks / stagflation / crisis 等 9 态），由规则引擎判定。 |
| Market Breadth | 市场宽度 | 市场中上涨/创新高个股占比等广度指标；宽度恶化常先于指数见顶。 |
| Credit Spread | 信用利差 | 信用债收益率与无风险利率之差（如 HY OAS / IG OAS），衡量信用风险溢价。 |
| Tail Risk | 尾部风险 | 极端、低概率但高冲击事件的风险（厚尾分布）。 |
| Drawdown | 回撤 | 资产从近期高点回落的幅度（如 52 周最大回撤）。 |
| Earnings Guidance | 业绩指引 | 公司对下一期业绩的前瞻性指导，财报日历的重要事件。 |
| Fund Flow | 资金流 | 资金进出市场的方向与规模；MVP 用成交/量能代理指标（Estimated/Proxy）。 |
| Implied Volatility | 隐含波动率 | 由期权价格反推的市场对未来波动预期（如 VIX）。 |
| Realized Volatility | 实现波动率 | 由历史收益率实际计算的波动率。 |
| Volatility Risk Premium | 波动率风险溢价 | 隐含波动率与实现波动率之差，反映期权卖方补偿。 |
| Liquidity Stress | 流动性压力 | 市场或金融体系流动性紧张程度（如回购利率飙升、利差走阔）。 |
| Support Level | 支撑位 | 价格下方可能获得买盘支撑的水平。 |
| Resistance Level | 阻力位 | 价格上方可能遇到卖压的水平。 |
| Confidence | 置信度 | 模型对风险判断的把握程度（0-1），由数据质量/覆盖度/信号一致性合成。 |

## 2. 风险模型维度

| English | 中文 | 说明 |
|---|---|---|
| Macro Risk | 宏观风险 | 实际利率、收益率曲线、通胀、就业等宏观维度。 |
| Liquidity & Credit Risk | 流动性与信用风险 | 联储资产负债表、逆回购、信用利差等维度。 |
| Equity Market Structure | 股票市场结构 | 市场宽度、新高新低、龙头集中度等结构维度。 |
| Volatility Risk | 波动率风险 | VIX、期限结构、实现/隐含波动率等维度。 |
| Cross-Asset Confirmation | 跨资产确认 | 多资产类别同时确认同一风险信号（9 项信号命中率）。 |
| Trend Risk | 趋势风险 | 价格相对均线、回撤、动量等趋势维度。 |
| Coverage | 覆盖度 | 有数据指标占该维全部指标的比例（0-1）。 |
| Data Quality | 数据质量 | 数据集质量评分（0-1），降级时下调。 |

## 3. 指标与市场术语

| English | 中文 | 说明 |
|---|---|---|
| Percentile | 百分位 | 当前值在 5Y 历史窗口中的分位（0-100）。 |
| Z-Score | Z 分数 | 当前值相对历史均值的标准差倍数。 |
| Momentum | 动量 | 一段时间内的价格趋势强度（如 3M 动量）。 |
| Moving Average (MA) | 移动平均线 | 如 MA50 / MA200（50/200 日均线）。 |
| Relative Strength | 相对强弱 | 某资产相对基准（如 SPX）的表现。 |
| New Highs / New Lows | 新高 / 新低 | 创 52 周新高的股票数 / 新低股票数。 |
| Breadth Above MA200 | 200 日均线上方比例 | 站上 200 日均线的个股占比（市场宽度代理）。 |
| Equal-Weight vs Cap-Weight | 等权与市值加权背离 | 等权指数与加权指数表现分化，反映宽度。 |
| OAS (Option-Adjusted Spread) | 期权调整利差 | 扣除嵌入期权后的信用利差；HY=高收益债、IG=投资级债。 |
| VIX | VIX 指数 | CBOE 波动率指数（隐含波动率）。 |
| VIX Term Structure | VIX 期限结构 | 不同期限 VIX 的相对关系（倒挂=压力信号）。 |
| ATR | 平均真实波幅 | 波动率技术指标。 |
| RSI | 相对强弱指标 | 0-100 动量振荡指标。 |
| MACD | 指数平滑异同移动平均 | 趋势跟踪指标。 |
| OBV / MFI | 能量潮 / 资金流量指标 | 资金流代理指标。 |
| Fed Funds Futures | 联邦基金期货 | 用于自算美联储会议概率（CME FedWatch 方法论）。 |
| EFFR | 有效联邦基金利率 | 联邦基金市场实际成交利率，FedWatch 概率计算的锚点。 |
| Real Rate | 实际利率 | 名义利率减通胀预期（如 10Y TIPS 收益率 DFII10）。 |
| Yield Curve | 收益率曲线 | 不同期限国债收益率关系；10Y-2Y 倒挂是衰退预警。 |
| Defensive Sector | 防御板块 | 公用事业/必需消费等低贝塔板块。 |
| Cyclical Sector | 周期板块 | 工业/材料/能源等高贝塔板块。 |
| Liquidity Trap | 流动性陷阱 | 货币政策失效的状态（非 MVP 范围，备查）。 |

## 4. 资产与数据

| English | 中文 | 说明 |
|---|---|---|
| Cross-Asset | 跨资产 | 股票/债券/汇率/商品/加密等多资产类别。 |
| Equities | 股票 / 美股 | 本产品"Equities"页展示美股 5 只（NVDA/AVGO/MU/AMD/TSLA）+ A 股存储 11 只。 |
| A-Shares | A 股 | 沪深交易所上市股票（如 603986.SH 兆易创新）。 |
| Crypto | 加密货币 | 如 BTC（比特币）。 |
| Semiconductors | 半导体 | 主题板块（SOXX 等）。 |
| Memory Sector | 存储板块 | 内存/存储相关个股与价格（DRAM/NAND 现货价代理）。 |
| Metals | 金属 | 铜等工业金属（宏观周期代理）。 |
| Catalysts | 催化剂 / 关键事件 | 即将发生的可能影响市场的宏观/财报事件。 |
| Earnings Calendar | 财报日历 | 上市公司财报发布日期。 |
| Economic Calendar | 经济日历 | 宏观数据发布日程。 |
| BEA Release Schedule | 美国经济分析局发布日程 | BEA 官方计划发布时间；仅包含发布时间，不含实际值或预测值。 |

BEA release titles are kept verbatim in English and use these Chinese UI translations:

| English source title | 中文 UI 标题 |
|---|---|
| Activities of U.S. Affiliates of Foreign Multinational Enterprises | 外国跨国企业在美关联企业经营活动 |
| Activities of U.S. Multinational Enterprises | 美国跨国企业经营活动 |
| Arts and Cultural Production Satellite Account | 艺术与文化生产卫星账户 |
| Corporate Profits | 企业利润 |
| Direct Investment by Country and Industry | 按国家与行业划分的直接投资 |
| Distribution of U.S. Personal Income | 美国个人收入分布 |
| Experimental Research and Development Economic Statistics | 研发经济试验统计 |
| Experimental Research and Development Satellite Account | 研发卫星账户试验统计 |
| GDP by County and Personal Income by County | 各县国内生产总值与个人收入 |
| Global Value Chains (Trade in Value Added) | 全球价值链与增加值贸易 |
| Gross Domestic Product | 国内生产总值 |
| Gross Domestic Product by County and Metropolitan Area and Personal Income by County and Metropolitan Area | 各县与都市区国内生产总值及个人收入 |
| Gross Domestic Product by County and Personal Income by County | 各县国内生产总值及个人收入 |
| Gross Domestic Product by State and Personal Income by State | 各州国内生产总值与个人收入 |
| Gross Domestic Product for Puerto Rico | 波多黎各国内生产总值 |
| Integrated BEA-BLS Industry-Level Production Account | 经济分析局与劳工统计局行业综合生产账户 |
| Marine Economy Satellite Account | 海洋经济卫星账户 |
| New Foreign Direct Investment in the United States | 新增对美外国直接投资 |
| Outdoor Recreation Economic Statistics | 户外休闲经济统计 |
| Personal Income and Outlays | 个人收入和支出 |
| Prototype Distribution of State Personal Income | 各州个人收入分布试算 |
| Real Consumer Spending by State and Real Personal Income by State | 各州实际消费支出与实际个人收入 |
| Real Personal Consumption Expenditures by State and Real Personal Income by State | 各州实际个人消费支出与实际个人收入 |
| Services Supplied Through Affiliates | 通过关联企业提供的服务 |
| Space Economy Statellite Account | 太空经济卫星账户 |
| Trade in Value Added | 增加值贸易 |
| Travel and Tourism Satellite Account | 旅行与旅游卫星账户 |
| U.S. Household Production Economic Statistics | 美国家庭生产经济统计 |
| U.S. International Investment Position | 美国国际投资头寸 |
| U.S. International Trade in Goods and Services | 美国货物与服务国际贸易 |
| U.S. International Transactions | 美国国际收支交易 |
| U.S. International Transactions and Investment Position | 美国国际收支交易与投资头寸 |
| U.S. Trade in Services Detailed Tables | 美国服务贸易明细表 |

## 5. 状态与数据质量

| English | 中文 | 说明 |
|---|---|---|
| Fresh | 新鲜 | 数据按期望频率正常更新。 |
| Delayed | 延迟 | 数据更新落后于期望频率（1.5×~3× 间隔）。 |
| Stale | 过期 | 数据长时间未更新（>3× 间隔），前端显示"已过期"角标。 |
| Missing | 缺失 | 从未有数据 / 文件缺失，前端显示空状态。 |
| Degraded | 降级 | 部分数据源失败回退（与时间无关），降低模型置信度。 |
| Proxy Indicator | 代理指标 | 无直接数据时用间接指标（标 Estimated/Proxy）。 |
| Evidence Ref | 证据引用 | AI 结论引用的证据索引（dataset+path+metric+value）。 |

## 6. 术语使用规则

1. 中英文 UI 文案必须使用上表术语（如 `市场状态` 而非 `市场体制`、`市场宽度` 而非 `市场广度`）。
2. 资产代码保持大写（`NVDA`、`603986.SH`、`BTC`），不翻译。
3. 数字/日期/货币格式遵循 PRD §8.9：中文 `2026年8月3日` / 英文 `Aug 3, 2026`；中文 `上涨 2.35%` / 英文 `Up 2.35%`；中文 `3.2万亿美元` / 英文 `$3.2T`。
4. AI 简报必须遵循本术语表；新术语先在本表登记后再用于 prompt 与文案。
