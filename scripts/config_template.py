"""监控参数模板。复制成 config.py 再改。

================== 先把你的要求写在这里 ==================

把出行人的硬性要求用大白话记在本段，改任何配置前先读。下面是示例格式，
按自己的情况替换。踩过的坑建议也留在这里，它们比配置项本身更容易被忘掉。

行程       出发地 ⇄ 目的地，几天几晚，可接受的日期组合
           日期是否可动？如果不可动，就别再建议改日期。

预算       含行李来回总价上限。这应当是真实预算。
           ★ 绝不能把目标价调到高于当前可订价——那样通知只是在复述
             "现在能买"，监控就失去意义了。

行李       是否需要寄舱行李，几件。注意"只有去程带行李"不算满足要求。

转机       要排除的中转地。排一个地区就要把该地区所有机场都列上
           （例如台湾：TPE / TSA / KHH / RMQ），只写主机场会漏。
           从外地机场出发时，还要排除中转回本地机场的行程——
           OTA 会把"坐船/大巴回本地机场再飞"当成航班卖。

时间       去程最晚几点到目的地、回程最晚几点到家、接不接受跨夜和红眼。
           注意中文里"下午就可以抵达"通常是上限（最晚下午到），
           不是"必须下午到"。搞反会差出几百块，不确定就问。

价格真实性 ★ 只报点进下单面板、确认行李符合要求的"已验证"价。
           ★ 取最低价必须对**过滤后**的集合取，不能对原始列表取。

货币       统一用哪种货币报价，其他币种先换算。

==========================================================

下面的值是一个示例航线（香港→首尔），换航线时逐项核对，
尤其是 TRIP_DEST / CURRENCY_SYMBOL / TRIP_DOMAIN / BAGGAGE_FEE 这四项。
"""

# ---- 航线 ----
# 可以同时盯多个出发机场。哪条先跌到目标价就通知哪条。
#   origin/dest        Google Flights 用的 IATA 机场代码
#   trip_origin/dest   Trip.com 自己的小写代码（sel = 首尔全部机场，覆盖 ICN + GMP）
#   ground             从家到该机场的**来回**地面交通费（本币），会计进总价，
#                      否则广州 3,200 的票看起来比香港 3,400 便宜，实际并不是。
#                      下面是粗估值，按你自己的实际走法改。
ROUTES = [
    {"name": "香港",   "origin": "HKG", "dest": "ICN",
     "trip_origin": "hkg", "trip_dest": "sel", "ground": 0},
    {"name": "澳门",   "origin": "MFM", "dest": "ICN",
     "trip_origin": "mfm", "trip_dest": "sel", "ground": 340},   # 港澳船票来回
    {"name": "深圳",   "origin": "SZX", "dest": "ICN",
     "trip_origin": "szx", "trip_dest": "sel", "ground": 200},   # 高铁/直通巴士来回
    {"name": "广州",   "origin": "CAN", "dest": "ICN",
     "trip_origin": "can", "trip_dest": "sel", "ground": 430},   # 高铁来回 + 机场线
]

# ---- 日期 ----
# 方式一：显式列出候选日期
OUTBOUND_DATES = ["2027-04-02", "2027-04-03"]
RETURN_DATES = ["2027-04-06", "2027-04-07"]

# 方式二：给一个出发日期区间，配合下面的 TRIP_NIGHTS 自动生成所有组合。
# 例如 ("2027-04-01", "2027-04-12") + TRIP_NIGHTS=4 会生成 10/1→10/5 一直到 10/12→10/16。
# 设了这个就会忽略上面两个列表。区间越长每轮越慢。
OUTBOUND_DATE_RANGE = None
# 只盯固定晚数（回程日 - 去程日）。4 = 五天四晚。
# 这会把上面的日期叉乘收窄，例如 4/2→4/6 和 4/3→4/7 两组。
# 设成 None 则监控全部叉乘组合。
TRIP_NIGHTS = 4

# ---- 时间约束（24 小时制，当地时间）----
# 去程：当天这个时刻前抵达目的地。(0, 18) = 不限起飞时间，当天下午前到。
OUT_ARRIVE_WINDOW = (0, 18)
# 回程：抵达出发地的时间窗。(0, 24) = 当天到就行，红眼也接受。
RET_ARRIVE_WINDOW = (0, 24)
# 回程偏好时段。不是硬性过滤，只额外单独报一个此窗口内的最优解。
RET_PREFERRED_ARRIVE = (12, 20)
# 不接受跨夜航班（Google 标 "+1"，Trip 标 "+1"）
REQUIRE_SAME_DAY_ARRIVAL = True

# ---- 转机限制 ----
# Google Flights 把中转标成 IATA 代码（"3 hr 20 min TPE"）
EXCLUDE_LAYOVER_AIRPORTS = {"TPE", "TSA", "KHH", "RMQ",  # 台湾
                            "MNL"}  # 马尼拉：10 月台风季，且绕路 7 小时
# Trip.com 把中转标成城市名（"3h 20m in Taipei"），所以要另列一份
EXCLUDE_LAYOVER_CITIES = {"taipei", "taiwan", "kaohsiung", "taichung", "taoyuan",
                          "台北", "台灣", "台湾", "高雄", "桃園", "桃园",
                          "manila", "马尼拉", "馬尼拉",
                          # 从澳门/深圳/广州出发时，Trip.com 会把"坐船/大巴回香港
                          # 机场再飞"当成航班卖（承运方写着港珠澳快线、珠江船务）。
                          # 那等于白跑一趟外地机场，一律排除。
                          "hong kong", "香港"}

# Google 的「Separate tickets」= 把两张单程票拼成一个行程。廉航不跟别家联程，
# 所以廉航去程的回程几乎全是这种。两程都直飞时没有转机衔接风险，默认允许，
# 只在结果里标注；介意的话改成 True。
EXCLUDE_SEPARATE_TICKETS = False

# ---- 行李 ----
# Trip.com 会点进下单面板读出真实的两程行李明细，那条路径不需要这张表。
# 这张表只用于 Google Flights——它整个不提供寄舱行李过滤器，只能估。
# 0 = 票价本身已含寄舱行李额。表里没有的航司按 DEFAULT_BAGGAGE_FEE 估。
# 注意：这些数字是按航线查的，换航线务必重新核对。
BAGGAGE_FEE = {
    "Cathay Pacific": 0,
    "Korean Air": 0,
    "Asiana Airlines": 0,
    "Hong Kong Airlines": 0,
    "Air Premia": 0,
    "Hong Kong Express": 310,   # 官网购票同步加购 20kg 价，事后补加为 380
    "HK Express": 310,
    "Jeju Air": 260,
    "EASTAR JET": 260,
    "Eastar Jet": 260,
    "T'way Air": 260,
    "Tway Air": 260,
    "Air Busan": 260,
    "Jin Air": 260,
}
DEFAULT_BAGGAGE_FEE = 300

# ---- 目标 ----
TARGET_PRICE = 3500  # 含行李的来回总价（本币）

# ---- 地区与货币 ----
CURRENCY = "HKD"
# 页面上实际显示的货币符号，用来解析价格。换币种必须一起改，
# 否则价格正则匹配不到，会静默地一个航班都抓不到。
CURRENCY_SYMBOL = "HK$"
# Trip.com 的地区站点。换地区改这里：www.trip.com / jp.trip.com / sg.trip.com / uk.trip.com
TRIP_DOMAIN = "hk.trip.com"
TRIP_LOCALE = "en-HK"

# ---- 抓取深度 ----
# 每个日期组合点进几个去程候选查回程
MAX_OUTBOUND_PROBES = 6
# Trip.com 每个候选之间要重新导航 + 开下单面板验证，慢得多，单独限制
TRIP_OUTBOUND_PROBES = 4

HEADLESS = True


def money(n):
    """按上面配置的符号格式化金额。所有输出都走这里，换币种不用改代码。"""
    return f"{CURRENCY_SYMBOL}{n:,}"
