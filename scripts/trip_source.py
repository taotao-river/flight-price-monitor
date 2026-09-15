"""Trip.com 数据源。

比 Google Flights 好在两点：价格更低，而且每张卡片直接标注是否含寄舱行李，
所以标了含行李的行程可以按真实数据算，只有没标的才退回到航司费率表估算——
Google 那边则是整份数据都只能估。
"""
import re
from dataclasses import dataclass

from config import money
import config as C

CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
PRICE_RE = re.compile(re.escape(C.CURRENCY_SYMBOL) + r"\s?([\d,]+)")
STOPS_RE = re.compile(r"^(Direct|\d+\s*stops?)$", re.I)
# 中转写成 "2h 55m in Shanghai" / "22h 25m in Beijing"
LAYOVER_RE = re.compile(r"^\d+h(?:\s*\d+m)?\s+in\s+(.+)$", re.I)


SEARCH_URL = (f"https://{C.TRIP_DOMAIN}/flights/showfarefirst?dcity={{o}}&acity={{d}}"
              "&ddate={dd}&rdate={rd}&triptype=rt&class=y&quantity=1"
              f"&locale={C.TRIP_LOCALE}&curr={C.CURRENCY}")


@dataclass
class TripFlight:
    airline: str
    depart: str
    arrive: str
    depart_h: float
    arrive_h: float
    next_day: bool
    stops: str
    price: int
    bag_included: bool
    layovers: tuple = ()

    def key(self):
        return (self.airline, self.depart, self.arrive, self.price, self.layovers)

    def via_excluded(self):
        return any(any(t in c.lower() for t in C.EXCLUDE_LAYOVER_CITIES)
                   for c in self.layovers)

    def label(self):
        bag = "含行李" if self.bag_included else "不含行李"
        route = "直飞" if not self.layovers else "经" + "/".join(self.layovers)
        return (f"{self.airline} {self.depart}→{self.arrive}"
                f"{'+1' if self.next_day else ''} ({route}, {bag})")


def _hm(s):
    m = CLOCK_RE.match(s)
    return int(m.group(1)) + int(m.group(2)) / 60 if m else None


def parse_card(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 6:
        return None

    times = [(i, l) for i, l in enumerate(lines) if CLOCK_RE.match(l)]
    if len(times) < 2:
        return None
    dep_i, dep_s = times[0]
    arr_i, arr_s = times[1]

    price = None
    for l in lines:
        m = PRICE_RE.search(l)
        if m:
            price = int(m.group(1).replace(",", ""))
            break
    if price is None:
        return None

    # 航司名在第一个时间之前、且不是徽章文字
    airline = "?"
    for l in reversed(lines[:dep_i]):
        if l.lower() not in ("cheapest direct", "checked baggage included",
                             "carry-on baggage included") and "baggage" not in l.lower():
            airline = l
            break

    stops = next((l for l in lines if STOPS_RE.match(l)), "?")
    # "+1" 单独成行，出现在到达时间之后
    next_day = any(l.startswith("+1") or l == "+1" for l in lines[arr_i:])

    layovers = tuple(m.group(1).strip() for m in
                     (LAYOVER_RE.match(l) for l in lines) if m)

    return TripFlight(
        airline=airline, depart=dep_s, arrive=arr_s,
        depart_h=_hm(dep_s), arrive_h=_hm(arr_s), next_day=next_day,
        stops=stops, price=price,
        bag_included="checked baggage included" in text.lower(),
        layovers=layovers,
    )


FARE_CARD = ".is-fareok-card"
# 「Checked baggage:」后面跟到下一个区块之前的文字
CHECKED_RE = re.compile(r"Checked baggage:\s*([^\n]*)", re.I)


def fare_bag_status(text):
    """判断某个票价档是否两程都带寄舱行李。

    Trip 的措辞有三种：
      "Included"                                → 两程都有
      "1 × 23 kg (departure), None (return)"    → 只有一程，不合要求
      "View details"                            → 没展开看不到，保守起见当作不合要求
    """
    m = CHECKED_RE.search(text)
    if not m:
        return "unknown", ""
    detail = m.group(1).strip()
    low = detail.lower()
    if "none" in low:
        return "partial", detail
    if low.startswith("included"):
        return "ok", detail
    if "kg" in low and "departure" in low and "return" in low:
        return "ok", detail
    return "unknown", detail


async def verify_fare(page, log=print):
    """读「Select Fare」面板——这里的档位才是真实可订的价。

    列表页那个「Checked baggage included」徽章只代表某一程有行李，
    实测同一行程列表报 HK$3,451（回程无行李），面板里两程都带行李要 HK$3,800。
    返回 (真实价, 行李说明) 或 None。
    """
    try:
        await page.wait_for_selector(FARE_CARD, timeout=20000)
    except Exception:
        return None
    await page.wait_for_timeout(2000)

    best = None
    for el in await page.query_selector_all(FARE_CARD):
        try:
            text = await el.inner_text()
        except Exception:
            continue
        m = PRICE_RE.search(text)
        if not m:
            continue
        price = int(m.group(1).replace(",", ""))
        status, detail = fare_bag_status(text)
        if status != "ok":
            continue
        if best is None or price < best[0]:
            best = (price, detail)
    return best


def bag_cost(f_out, f_ret):
    """回程卡片的价已是来回总价。Trip 标了含行李就按 0 算（真实数据），
    没标才用航司费率表补两程行李费（估算）。"""
    if f_ret.bag_included:
        return 0, False
    fee = C.BAGGAGE_FEE.get(f_out.airline.strip(), C.DEFAULT_BAGGAGE_FEE) + \
          C.BAGGAGE_FEE.get(f_ret.airline.strip(), C.DEFAULT_BAGGAGE_FEE)
    return fee, True


async def _click_label(page, name):
    try:
        el = page.locator(f"label:has-text('{name}')").first
        if await el.count() and await el.is_visible():
            await el.click(timeout=5000)
            await page.wait_for_timeout(3500)
            return True
    except Exception:
        pass
    return False


async def _sort_cheapest(page):
    try:
        el = page.locator("div.sort-type", has_text="Cheapest").first
        if await el.count():
            await el.click(timeout=5000)
            await page.wait_for_timeout(3500)
            return True
    except Exception:
        pass
    return False


async def _load_all(page, rounds=14):
    """结果列表是懒加载的。必须等数量连续两轮不变才算加载完 —— 只滚一次就抓
    会拿到半截列表（实测同一组合时而 28 班时而 11 班，结果差上千元）。"""
    prev, stable = -1, 0
    for _ in range(rounds):
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(2500)
        n = len(await page.query_selector_all(".result-item"))
        stable = stable + 1 if n == prev else 0
        prev = n
        if stable >= 3 and n > 0:
            break
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(1500)
    return prev


async def scrape(page, log=print):
    out, seen = [], set()
    for el in await page.query_selector_all(".result-item"):
        try:
            f = parse_card(await el.inner_text())
        except Exception:
            continue
        if f and f.key() not in seen:
            seen.add(f.key())
            out.append((f, el))
    return out


def acceptable(f, window):
    if C.REQUIRE_SAME_DAY_ARRIVAL and f.next_day:
        return False
    if not (window[0] <= f.arrive_h < window[1]):
        return False
    if f.via_excluded():
        return False
    return True


async def check_combo(page, out_date, ret_date, log=print):
    """返回 (最低含行李方案, 最低且下午抵港方案)，都已含寄舱行李。"""
    url = SEARCH_URL.format(o=C.TRIP_ORIGIN, d=C.TRIP_DEST,
                            dd=out_date, rd=ret_date)
    await page.goto(url, wait_until="domcontentloaded", timeout=70000)
    await page.wait_for_timeout(15000)

    # 不勾 UI 的「Direct」：它会砍掉便宜的非黑名单中转，而且在回程页会被重置。
    # 转机限制改在代码里按中转城市名判断，更可靠。
    # 不勾「Checked baggage included」：实测该过滤器 flaky，且会砍掉
    # 「裸票 + 自购行李」反而更便宜的组合。行李成本在下面按卡片标注逐个算。
    await _sort_cheapest(page)
    await _load_all(page)

    outs = await scrape(page)
    # 列表偶尔只返回一截（实测同一组合 11 班 vs 28 班），重载一次取较全的那份
    if len(outs) < 15:
        await page.goto(url, wait_until="domcontentloaded", timeout=70000)
        await page.wait_for_timeout(15000)
        await _sort_cheapest(page)
        await _load_all(page)
        again = await scrape(page)
        if len(again) > len(outs):
            log(f"  [Trip] {out_date} → {ret_date}: 列表被截断（{len(outs)} 班），"
                f"重载后取到 {len(again)} 班")
            outs = again

    if not outs:
        log(f"  [Trip] {out_date} → {ret_date}: 没抓到航班")
        return None, None

    cands = [(f, el) for f, el in outs if acceptable(f, C.OUT_ARRIVE_WINDOW)]
    cands.sort(key=lambda x: x[0].price)
    log(f"  [Trip] {out_date} → {ret_date}: 去程 {len(outs)} 班，符合条件 {len(cands)} 班")
    if not cands:
        for f, _ in sorted(outs, key=lambda x: x[0].price)[:12]:
            log(f"      被拒: {money(f.price)} {f.label()} arrive_h={f.arrive_h}")
        return None, None

    best = best_pref = None
    targets = [f.key() for f, _ in cands[:C.TRIP_OUTBOUND_PROBES]]

    for i, key in enumerate(targets):
        if i > 0:
            # 不用 go_back：实测返回后列表只恢复一半，重新导航才能保证状态干净
            await page.goto(url, wait_until="domcontentloaded", timeout=70000)
            await page.wait_for_timeout(12000)
            await _sort_cheapest(page)
            await _load_all(page)
        hit = next(((f, el) for f, el in await scrape(page) if f.key() == key), None)
        if hit is None:
            continue
        f_out, el = hit
        btn = await el.query_selector("button")
        try:
            await (btn or el).click(timeout=8000)
        except Exception:
            continue
        await page.wait_for_timeout(9000)

        if "ShowFareNext" not in page.url:  # 没跳到回程页
            continue

        await _load_all(page)
        rets = sorted([(f, e) for f, e in await scrape(page)
                       if acceptable(f, C.RET_ARRIVE_WINDOW)],
                      key=lambda x: x[0].price)
        if not rets:
            continue

        lo, hi = C.RET_PREFERRED_ARRIVE
        # 验证要逐个开面板，很慢，所以只验最便宜的那班 + 最便宜的下午抵港那班
        picks = [rets[0][0].key()]
        aft = next((f.key() for f, _ in rets if lo <= f.arrive_h < hi), None)
        if aft and aft not in picks:
            picks.append(aft)

        for key in picks:
            hit2 = next(((f, e) for f, e in await scrape(page) if f.key() == key), None)
            if hit2 is None:
                continue
            f_ret, rel = hit2

            verified = None
            try:
                await (await rel.query_selector("button") or rel).click(timeout=8000)
                await page.wait_for_timeout(9000)
                verified = await verify_fare(page, log)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(3000)
            except Exception:
                pass

            if verified:
                total, bags, detail, est = verified[0], 0, verified[1], False
            else:
                bags, est = bag_cost(f_out, f_ret)
                total, detail = f_ret.price + bags, ""

            cand = {"source": "Trip.com", "out_date": out_date, "ret_date": ret_date,
                    "fare": f_ret.price, "bags": bags, "total": total,
                    "bag_estimated": est, "verified": bool(verified),
                    "bag_detail": detail, "out": f_out, "ret": f_ret}
            if best is None or cand["total"] < best["total"]:
                best = cand
            if lo <= f_ret.arrive_h < hi and (best_pref is None
                                              or cand["total"] < best_pref["total"]):
                best_pref = cand

    return best, best_pref
