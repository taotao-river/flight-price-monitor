"""监控参数模板。复制成 config.py 再改。

改完存盘即可，下一轮自动生效，不用重启 launchd。
下面的值是一个示例航线（香港→首尔），换航线时逐项核对，
尤其是 TRIP_DEST / CURRENCY_SYMBOL / TRIP_DOMAIN / BAGGAGE_FEE 这四项。
"""

# ---- 航线 ----
# Google Flights 用 IATA 机场代码
ORIGIN = "HKG"
DEST = "ICN"
# Trip.com 用它自己的小写城市/机场代码。城市代码（sel = 首尔全部机场）比
# 单个机场代码覆盖更广。可以先在 hk.trip.com 搜一次，从地址栏的 dcity/acity 抄。
TRIP_ORIGIN = "hkg"
TRIP_DEST = "sel"

# ---- 日期 ----
# 方式一：显式列出候选日期
OUTBOUND_DATES = ["2027-04-02", "2027-04-03"]
RETURN_DATES = ["2027-04-06", "2027-04-07"]

# 方式二：给一个出发日期区间，配合下面的 TRIP_NIGHTS 自动生成所有组合。
# 例如 ("2027-04-01", "2027-04-12") + TRIP_NIGHTS=4 会生成 4/1→4/5 一直到 4/12→4/16。
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
EXCLUDE_LAYOVER_AIRPORTS = {"TPE", "TSA", "KHH", "RMQ"}
# Trip.com 把中转标成城市名（"3h 20m in Taipei"），所以要另列一份
EXCLUDE_LAYOVER_CITIES = {"taipei", "taiwan", "kaohsiung", "taichung", "taoyuan",
                          "台北", "台灣", "台湾", "高雄", "桃園", "桃园"}

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
TARGET_PRICE = 3300  # 含行李的来回总价（本币）

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
MAX_OUTBOUND_PROBES = 10
# Trip.com 每个候选之间要重新导航 + 开下单面板验证，慢得多，单独限制
TRIP_OUTBOUND_PROBES = 8

HEADLESS = True


def money(n):
    """按上面配置的符号格式化金额。所有输出都走这里，换币种不用改代码。"""
    return f"{CURRENCY_SYMBOL}{n:,}"
