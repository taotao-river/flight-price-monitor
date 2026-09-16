"""香港 <-> 首尔 来回低价机票监控。

两个数据源：Trip.com 和 Google Flights。两边都按出发/抵达时间窗、当天抵达、
排除台湾转机过滤。

关键：搜索列表上的价常常订不到——列表标的「含行李」可能只有去程有，
Google 显示的 OTA 价点进去也常常消失。所以 Trip.com 这边会再点进下单面板，
读出真正两程都带一件寄舱行李的可订价。只有这种「已验证」的价才会触发通知。
"""
import asyncio
import csv
import fcntl
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

import config as C
from config import money
import trip_source

HERE = Path(__file__).parent
HISTORY = HERE / "history.csv"
LOG = HERE / "monitor.log"

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(AM|PM)\s*(\+\d+)?$", re.I)
PRICE_RE = re.compile(re.escape(C.CURRENCY_SYMBOL) + r"\s?([\d,]+)")
STOPS_RE = re.compile(r"^(Nonstop|\d+\s+stops?)$", re.I)
LAYOVER_RE = re.compile(r"\b(?:\d+\s*hr(?:\s*\d+\s*min)?|\d+\s*min)\s+([A-Z]{3})\b")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def to_24h(text):
    m = TIME_RE.match(text.strip())
    if not m:
        return None, False
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ap == "PM" and h != 12:
        h += 12
    elif ap == "AM" and h == 12:
        h = 0
    return h + mi / 60, bool(m.group(4))


def baggage_fee(airline):
    return C.BAGGAGE_FEE.get(airline.strip(), C.DEFAULT_BAGGAGE_FEE)


@dataclass
class Flight:
    depart: str
    arrive: str
    depart_h: float
    arrive_h: float
    next_day: bool
    airline: str
    stops: str
    price: int
    layovers: list = field(default_factory=list)
    separate_tickets: bool = False

    def key(self):
        return (self.depart, self.arrive, self.airline, self.price)

    def label(self):
        tag = " [拆票]" if self.separate_tickets else ""
        return f"{self.airline} {self.depart}→{self.arrive} ({self.stops}){tag}"


def parse_card(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # 详情版重复卡片会被压成一整行，行数不足直接丢弃
    if len(lines) < 6:
        return None

    times = [(i, l) for i, l in enumerate(lines) if TIME_RE.match(l)]
    if len(times) < 2:
        return None
    dep_i, dep_s = times[0]
    arr_i, arr_s = times[1]
    dep_h, _ = to_24h(dep_s)
    arr_h, next_day = to_24h(arr_s)
    if dep_h is None or arr_h is None:
        return None

    price = None
    for l in lines:
        m = PRICE_RE.search(l)
        if m:
            price = int(m.group(1).replace(",", ""))
            break
    if price is None:  # "Price unavailable"
        return None

    stops = next((l for l in lines if STOPS_RE.match(l)), "?")

    layovers = []
    for l in lines:
        if "–" in l or "—" in l:  # 跳过 HKG–ICN 航段行
            continue
        layovers.extend(LAYOVER_RE.findall(l))

    airline = lines[arr_i + 1] if arr_i + 1 < len(lines) else "?"
    if TIME_RE.match(airline) or PRICE_RE.search(airline):
        airline = "?"

    return Flight(
        depart=dep_s, arrive=arr_s, depart_h=dep_h, arrive_h=arr_h,
        next_day=next_day, airline=airline, stops=stops, price=price,
        layovers=layovers,
        separate_tickets="separate ticket" in text.lower(),
    )


async def expand_all(page, max_clicks=6):
    """Google 默认只显示约 1/3 的航班，其余藏在「View more flights」后面。
    不展开就会漏掉济州、Air Premia、德威这类，以及国泰的低价舱。"""
    for _ in range(max_clicks):
        clicked = False
        for pat in ("View more flights", "Show more flights", "more flights"):
            try:
                btn = page.get_by_role("button", name=pat)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=4000)
                    await page.wait_for_timeout(2500)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            return


async def scrape_list(page):
    """抓当前页面所有航班卡片并去重。"""
    out, seen = [], set()
    for ul in await page.query_selector_all("ul.Rk10dc"):
        for li in await ul.query_selector_all("li.pIav2d"):
            try:
                f = parse_card(await li.inner_text())
            except Exception:
                continue
            if f and f.key() not in seen:
                seen.add(f.key())
                out.append((f, li))
    return out


async def dismiss_consent(page):
    for label in ("Accept all", "Reject all", "I agree", "Got it"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click(timeout=3000)
                await page.wait_for_timeout(2500)
                return
        except Exception:
            pass


def in_window(hour, window):
    return window[0] <= hour < window[1]


def acceptable(f, arrive_window):
    if C.REQUIRE_SAME_DAY_ARRIVAL and f.next_day:
        return False
    if not in_window(f.arrive_h, arrive_window):
        return False
    if set(f.layovers) & C.EXCLUDE_LAYOVER_AIRPORTS:
        return False
    if C.EXCLUDE_SEPARATE_TICKETS and f.separate_tickets:
        return False
    return True


async def check_combo(page, route, out_date, ret_date):
    """返回该日期组合下、满足全部条件的最便宜来回方案（含行李费）。"""
    url = (
        f"https://www.google.com/travel/flights?hl=en&curr={C.CURRENCY}&gl=HK"
        f"&q=Flights%20from%20{route['origin']}%20to%20{route['dest']}"
        f"%20on%20{out_date}%20through%20{ret_date}"
    )
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(6000)
    await dismiss_consent(page)

    try:
        await page.wait_for_selector("ul.Rk10dc li.pIav2d", timeout=30000)
    except Exception:
        log(f"  {route['name']} {out_date} → {ret_date}: 去程列表未加载")
        return None

    await expand_all(page)
    outbounds = await scrape_list(page)
    cands = [(f, li) for f, li in outbounds if acceptable(f, C.OUT_ARRIVE_WINDOW)]
    cands.sort(key=lambda x: x[0].price)
    log(f"  {route['name']} {out_date} → {ret_date}: 去程 {len(outbounds)} 班，符合条件 {len(cands)} 班")
    if not cands:
        return None

    best = None       # 最便宜（只要求当天抵港）
    best_pref = None  # 最便宜且下午抵港

    # 只记录标识：go_back 之后页面重绘，之前抓到的 DOM handle 全部失效
    targets = [f.key() for f, _ in cands[:C.MAX_OUTBOUND_PROBES]]

    for i, key in enumerate(targets):
        if i > 0:  # 回到列表页后要重新展开才能再看到完整列表
            await expand_all(page)
        fresh = await scrape_list(page)
        hit = next(((f, li) for f, li in fresh if f.key() == key), None)
        if hit is None:
            continue
        f_out, li = hit

        try:
            await li.click(timeout=8000)
        except Exception as e:
            log(f"    点击去程 {f_out.label()} 失败: {type(e).__name__}")
            continue
        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_selector("ul.Rk10dc li.pIav2d", timeout=20000)
        except Exception:
            await page.go_back(wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            continue

        await expand_all(page, max_clicks=4)
        for f_ret, _ in await scrape_list(page):
            if not acceptable(f_ret, C.RET_ARRIVE_WINDOW):
                continue
            bags = baggage_fee(f_out.airline) + baggage_fee(f_ret.airline)
            total = f_ret.price + bags
            cand = {
                "route": route, "ground": route["ground"],
                "out_date": out_date, "ret_date": ret_date,
                "fare": f_ret.price, "bags": bags, "total": total,
                "out": f_out, "ret": f_ret,
            }
            if best is None or total < best["total"]:
                best = cand
            if in_window(f_ret.arrive_h, C.RET_PREFERRED_ARRIVE):
                if best_pref is None or total < best_pref["total"]:
                    best_pref = cand

        await page.go_back(wait_until="domcontentloaded")
        await page.wait_for_timeout(4500)

    if best:
        best["pref"] = best_pref
    return best


def _applescript_str(s):
    """AppleScript 只接受双引号字符串，Python 的 repr 会给出单引号导致语法错误。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osa(script, wait=True):
    try:
        if wait:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                log(f"  osascript 失败: {r.stderr.strip()[:120]}")
            return r.stdout.strip()
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"  osascript 异常: {e}")
    return ""


def notify(title, message, url=None):
    """到价通知：横幅 + 连续提示音 + 语音播报 + 一个需要手动关掉的置顶弹窗。"""
    _osa(f"display notification {_applescript_str(message)} "
         f"with title {_applescript_str(title)} sound name \"Glass\"")

    try:
        subprocess.Popen(
            ["bash", "-c",
             "for i in 1 2 3; do afplay /System/Library/Sounds/Glass.aiff; sleep 0.2; done"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    _osa(f"say {_applescript_str('机票到价了，' + title)}", wait=False)

    # 模态弹窗会一直停在最前面直到手动关闭，跑在子进程里以免卡住抓取流程
    btns = '{"知道了", "去订票"}' if url else '{"知道了"}'
    dlg = (f'display alert {_applescript_str(title)} '
           f'message {_applescript_str(message)} as critical '
           f'buttons {btns} default button 1 giving up after 900')
    if url:
        dlg = (f'set r to ({dlg})\n'
               f'if button returned of r is "去订票" then '
               f'open location {_applescript_str(url)}')
    _osa(f'tell application "System Events" to activate\n{dlg}', wait=False)


HISTORY_COLUMNS = ["checked_at", "source", "route", "out_date", "ret_date", "fare_hkd",
                   "bags_hkd", "ground_hkd", "total_hkd", "verified", "bag_detail",
                   "out_airline", "out_depart", "out_arrive",
                   "ret_airline", "ret_depart", "ret_arrive"]


def record(results):
    # 表头只在建档时写一次。加过列之后旧表头会继续沿用，之后每行都比表头宽，
    # 整份数据按表头解析就是错位的——曾经整个上午 100 行全废。
    # 所以每次都核对表头，对不上就把旧档归档另起一份。
    if HISTORY.exists():
        with open(HISTORY) as f:
            head = f.readline().strip().split(",")
        if head != HISTORY_COLUMNS:
            stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
            HISTORY.rename(HISTORY.with_name(f"history-{stamp}.csv"))
            log(f"history.csv 表头与当前字段不符，已归档为 history-{stamp}.csv")

    new = not HISTORY.exists()
    with open(HISTORY, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HISTORY_COLUMNS)
        ts = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        for r in results:
            w.writerow([ts, r.get("source", "?"), r["route"]["name"],
                        r["out_date"], r["ret_date"],
                        r["fare"], r["bags"], r.get("ground", 0), r["total"],
                        bool(r.get("verified")), r.get("bag_detail", ""),
                        r["out"].airline, r["out"].depart, r["out"].arrive,
                        r["ret"].airline, r["ret"].depart, r["ret"].arrive])


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


async def _context(browser, stealth=False):
    ctx = await browser.new_context(
        locale="en-HK", timezone_id="Asia/Hong_Kong",
        user_agent=UA, viewport={"width": 1440, "height": 1100},
    )
    if stealth:
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return ctx


def date_pairs():
    """生成要监控的 (去程, 回程) 日期对。

    给了 OUTBOUND_DATE_RANGE 就按区间 + 固定晚数铺开；
    否则用显式的候选日期叉乘，再按晚数筛一遍。
    """
    if C.OUTBOUND_DATE_RANGE:
        if C.TRIP_NIGHTS is None:
            raise ValueError("用 OUTBOUND_DATE_RANGE 时必须同时设 TRIP_NIGHTS")
        lo, hi = (date.fromisoformat(d) for d in C.OUTBOUND_DATE_RANGE)
        days = (hi - lo).days
        return [((lo + timedelta(days=i)).isoformat(),
                 (lo + timedelta(days=i + C.TRIP_NIGHTS)).isoformat())
                for i in range(days + 1)]

    pairs = [(o, r) for o in C.OUTBOUND_DATES for r in C.RETURN_DATES]
    if C.TRIP_NIGHTS is not None:
        pairs = [(o, r) for o, r in pairs
                 if (date.fromisoformat(r) - date.fromisoformat(o)).days == C.TRIP_NIGHTS]
    return sorted(pairs)


def booking_url(r):
    rt = r["route"]
    if r.get("source") == "Trip.com":
        return trip_source.SEARCH_URL.format(
            o=rt["trip_origin"], d=rt["trip_dest"], dd=r["out_date"], rd=r["ret_date"])
    return (f"https://www.google.com/travel/flights?hl=en&curr={C.CURRENCY}&gl=HK"
            f"&q=Flights%20from%20{rt['origin']}%20to%20{rt['dest']}"
            f"%20on%20{r['out_date']}%20through%20{r['ret_date']}")


REQUIRED_KEYS = {"source", "route", "ground", "out_date", "ret_date",
                 "fare", "bags", "total", "out", "ret"}


def validate(r):
    """结果字典少字段要当场炸，不能等到最后写 CSV 时才发现——
    抓一轮要半小时，崩在末尾等于整轮白跑。"""
    missing = REQUIRED_KEYS - r.keys()
    if missing:
        raise KeyError(f"{r.get('source', '?')} 的结果缺少字段 {sorted(missing)}")
    return r


def _show(r):
    parts = [f"票价 {r['fare']:,}"]
    if r["bags"]:
        parts.append(f"行李 {r['bags']}")
    if r.get("ground"):
        parts.append(f"地面交通 {r['ground']}")
    bags = f" ({' + '.join(parts)})" if len(parts) > 1 else ""
    mark = "[已验证]" if r.get("verified") else "[未验证]"
    detail = f" 行李:{r['bag_detail']}" if r.get("bag_detail") else ""
    return (f"{money(r['total'])}{bags} {mark}{detail}"
            f" | 去 {r['out'].label()} | 回 {r['ret'].label()}")


LOCK = HERE / ".monitor.lock"


def acquire_lock():
    """防止多轮重叠。launchd 到点就无条件起一轮，上一轮没跑完就会撞上：
    两个进程往同一个日志里交错写、history.csv 混入两轮数据、抢内存导致被杀。
    返回文件对象（需保持引用，否则句柄被回收锁就没了）或 None。"""
    f = open(LOCK, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.write(f"{os.getpid()}\n")
    f.flush()
    return f


async def main():
    results = []
    pairs = date_pairs()
    log(f"监控 {len(C.ROUTES)} 个出发机场 × {len(pairs)} 个日期组合 = "
        f"{len(C.ROUTES)*len(pairs)} 组｜机场: {', '.join(r['name'] for r in C.ROUTES)}"
        f"{f'（{C.TRIP_NIGHTS} 晚）' if C.TRIP_NIGHTS is not None else ''}: "
        + ", ".join(f"{o}→{r}" for o, r in pairs))
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=C.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"])

        log("=== Trip.com（价格含行李，真实标注）===")
        tctx = await _context(browser, stealth=True)
        tpage = await tctx.new_page()
        for rt in C.ROUTES:
          for out_date, ret_date in pairs:
            try:
                best, pref = await trip_source.check_combo(
                    tpage, rt, out_date, ret_date, log=log)
            except Exception as e:
                log(f"  [Trip] {rt['name']} {out_date} → {ret_date} 出错: "
                    f"{type(e).__name__}: {e}")
                continue
            if best:
                best["pref"] = pref
                results.append(validate(best))
                log(f"    最低 {_show(best)}")
                if pref and pref["total"] != best["total"]:
                    log(f"    下午抵港 {_show(pref)}")
        await tctx.close()

        log("=== Google Flights（行李费按航司估算）===")
        gctx = await _context(browser)
        gpage = await gctx.new_page()
        for rt in C.ROUTES:
          for out_date, ret_date in pairs:
            try:
                r = await check_combo(gpage, rt, out_date, ret_date)
            except Exception as e:
                log(f"  {rt['name']} {out_date} → {ret_date} 出错: {e}")
                r = None
            if r:
                r["source"] = "Google"
                results.append(validate(r))
                log(f"    最低 {_show(r)}")
                pr = r.get("pref")
                if pr and pr["total"] != r["total"]:
                    pr["source"] = "Google"
                    log(f"    下午抵港 {_show(pr)}")
        await gctx.close()
        await browser.close()

    if not results:
        log("本轮没有拿到任何符合条件的组合")
        return 1

    record(results)
    results.sort(key=lambda r: r["total"])
    best = results[0]
    prefs = [r["pref"] for r in results if r.get("pref")]

    log("—— 本轮汇总（含行李，由低到高）——")
    for r in results:
        pr = r.get("pref")
        extra = f"  (下午抵港 {money(pr['total'])})" if pr and pr["total"] != r["total"] else ""
        log(f"  [{r.get('source','?'):<8}] {r['route']['name']} "
            f"{r['out_date']} → {r['ret_date']}: "
            f"{money(r['total'])} {'已验证' if r.get('verified') else '未验证'}{extra}")

    # 只有点进下单面板、确认两程都带寄舱行李的价才算数。
    # 列表价和 Google 的价都可能是点进去就没有的「诱饵价」，不拿来发通知。
    under = [r for r in results + prefs
             if r["total"] <= C.TARGET_PRICE and r.get("verified")]
    if under:
        hit = min(under, key=lambda r: r["total"])
        aft = C.RET_PREFERRED_ARRIVE[0] <= hit["ret"].arrive_h < C.RET_PREFERRED_ARRIVE[1]
        msg = (f"{money(hit['total'])} 已验证 | {hit['route']['name']}出发 | "
               f"{hit['out_date'][5:]}去 {hit['ret_date'][5:]}回 | "
               f"{hit['out'].airline} {hit['out'].depart} 出发 | "
               f"{hit['ret'].airline} {hit['ret'].arrive} 抵港{'（下午）' if aft else ''} | "
               f"行李 {hit.get('bag_detail') or '两程各一件'}")
        log(f"*** 命中目标价 *** {msg}")
        notify(f"机票到价 {money(hit['total'])}", msg, booking_url(hit))
    else:
        cheap_unverified = [r for r in results if r["total"] <= C.TARGET_PRICE]
        if cheap_unverified:
            c = min(cheap_unverified, key=lambda r: r["total"])
            log(f"有 {money(c['total'])} 低于目标但未通过下单页验证（来源 {c.get('source')}），"
                f"不发通知——这类价点进去常拿不到")
        log(f"最低 {money(best['total'])}"
            f"{'（已验证）' if best.get('verified') else '（未验证）'}，"
            f"距目标 {money(C.TARGET_PRICE)} 还差 {money(best['total'] - C.TARGET_PRICE)}")
    return 0


if __name__ == "__main__":
    _lock = acquire_lock()
    if _lock is None:
        log("上一轮还在跑，本轮跳过（避免两个进程抢资源、日志交错）")
        sys.exit(0)
    try:
        sys.exit(asyncio.run(main()))
    finally:
        _lock.close()
