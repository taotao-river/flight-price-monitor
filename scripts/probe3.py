"""诊断：展开全部航班，列出某去程下所有回程，并标出被哪条规则拒掉。

用法: python probe3.py [出发日 回程日]   不给参数则用 config 里的第一组
"""
import asyncio
import sys
from playwright.async_api import async_playwright

from config import money
import monitor
import config as C
from monitor import scrape_list, dismiss_consent, in_window, expand_all, baggage_fee

_first = monitor.date_pairs()[0]
OUT_DATE = sys.argv[1] if len(sys.argv) > 1 else _first[0]
RET_DATE = sys.argv[2] if len(sys.argv) > 2 else _first[1]


def reasons(f, window):
    r = []
    if C.REQUIRE_SAME_DAY_ARRIVAL and f.next_day:
        r.append("跨夜")
    if not in_window(f.arrive_h, window):
        r.append(f"抵达{f.arrive}不在窗口")
    bad = set(f.layovers) & C.EXCLUDE_LAYOVER_AIRPORTS
    if bad:
        r.append(f"经{'/'.join(bad)}")
    if C.EXCLUDE_SEPARATE_TICKETS and f.separate_tickets:
        r.append("拆票")
    return r


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(locale="en-HK", timezone_id="Asia/Hong_Kong",
                                  viewport={"width": 1440, "height": 1400})
        pg = await ctx.new_page()
        url = (f"https://www.google.com/travel/flights?hl=en&curr=HKD&gl=HK"
               f"&q=Flights%20from%20HKG%20to%20ICN%20on%20{OUT_DATE}%20through%20{RET_DATE}")
        await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        await dismiss_consent(pg)
        await pg.wait_for_selector("ul.Rk10dc li.pIav2d", timeout=30000)
        await expand_all(pg)

        outs = await scrape_list(pg)
        ok = [(f, li) for f, li in outs if not reasons(f, C.OUT_ARRIVE_WINDOW)]
        ok.sort(key=lambda x: x[0].price)

        print(f"{OUT_DATE} → {RET_DATE}   去程共 {len(outs)} 班，符合条件 {len(ok)} 班")
        print("=== 符合条件的去程（价为来回总价，未含行李）===")
        for f, _ in ok:
            print(f"  {money(f.price):<8} {f.airline:<20} {f.depart:>8} → {f.arrive:<9} "
                  f"行李费/程 HK${baggage_fee(f.airline)}")

        targets = [f.key() for f, _ in ok[:4]]
        for i, key in enumerate(targets):
            if i > 0:
                await expand_all(pg)
            hit = next(((f, li) for f, li in await scrape_list(pg) if f.key() == key), None)
            if hit is None:
                print(f"\n(找不到 {key}，跳过)")
                continue
            f_out, li = hit
            print(f"\n{'='*70}\n点进 {f_out.airline} {f_out.depart} (列表价 {money(f_out.price)})")
            await li.click()
            await pg.wait_for_timeout(6000)
            await pg.wait_for_selector("ul.Rk10dc li.pIav2d", timeout=25000)
            await expand_all(pg, max_clicks=4)
            rets = await scrape_list(pg)
            print(f"回程 {len(rets)} 班：")
            for f, _ in sorted(rets, key=lambda x: x[0].price)[:14]:
                rr = reasons(f, C.RET_ARRIVE_WINDOW)
                bags = baggage_fee(f_out.airline) + baggage_fee(f.airline)
                pref = "" if in_window(f.arrive_h, C.RET_PREFERRED_ARRIVE) else "  (非下午抵港)"
                mark = (f"✓ 含行李合计 {money(f.price + bags)}{pref}"
                        if not rr else "✗ " + ", ".join(rr))
                print(f"  {money(f.price):<8} {f.airline:<20} {f.depart:>8} → {f.arrive:<9} {mark}")
            await pg.go_back(wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)

        await b.close()


asyncio.run(main())
