"""诊断：展开全部航班 + 按价格排序，看默认视图到底漏了什么。"""
import asyncio
import sys
from playwright.async_api import async_playwright

from config import money
import monitor
from monitor import scrape_list, dismiss_consent

_first = monitor.date_pairs()[0]
OUT_DATE = sys.argv[1] if len(sys.argv) > 1 else _first[0]
RET_DATE = sys.argv[2] if len(sys.argv) > 2 else _first[1]


async def expand_all(pg):
    """点掉所有「View more flights」之类的展开按钮。"""
    for _ in range(6):
        hit = False
        for pat in ("View more flights", "Show more flights", "more flights"):
            try:
                btn = pg.get_by_role("button", name=pat)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=4000)
                    await pg.wait_for_timeout(3000)
                    hit = True
                    break
            except Exception:
                pass
        if not hit:
            return


async def sort_by_price(pg):
    try:
        btn = pg.get_by_role("button", name=lambda n: n and "Sorted by" in n)
        if not await btn.count():
            btn = pg.locator('button:has-text("Sorted by")')
        if await btn.count():
            await btn.first.click(timeout=5000)
            await pg.wait_for_timeout(1500)
            opt = pg.get_by_role("option", name="Price")
            if not await opt.count():
                opt = pg.get_by_role("menuitemradio", name="Price")
            if await opt.count():
                await opt.first.click(timeout=4000)
                await pg.wait_for_timeout(4000)
                return True
            await pg.keyboard.press("Escape")
    except Exception as e:
        print(f"  排序切换失败: {e}")
    return False


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

        before = await scrape_list(pg)
        print(f"{OUT_DATE} → {RET_DATE}")
        print(f"默认视图去程: {len(before)} 班")

        sorted_ok = await sort_by_price(pg)
        await expand_all(pg)
        after = await scrape_list(pg)
        print(f"展开+按价排序后: {len(after)} 班 (排序切换 {'成功' if sorted_ok else '失败'})\n")

        print("=== 全部去程（按价格）===")
        for f, _ in sorted(after, key=lambda x: x[0].price):
            tag = " [拆票]" if f.separate_tickets else ""
            lay = f" via {','.join(f.layovers)}" if f.layovers else ""
            print(f"  {money(f.price):<8} {f.airline:<22} {f.depart:>8} → {f.arrive:<10}"
                  f" {f.stops}{lay}{tag}")

        airlines = sorted({f.airline for f, _ in after})
        print(f"\n出现的航司: {', '.join(airlines)}")

        await b.close()


asyncio.run(main())
