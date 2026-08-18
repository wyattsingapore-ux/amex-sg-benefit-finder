#!/usr/bin/env python3
"""Eatigo importer v2: robust pagination control activation.

Eatigo renders pagination numbers in non-semantic elements. The underlying
resilient importer originally searched only buttons/anchors/role=button and
therefore could see 339 results but could not activate page 2. This wrapper
replaces only the pagination activation functions and reuses the validated
list/address/LC matching logic from augment_eatigo_resilient.
"""
from __future__ import annotations

from scripts import augment_eatigo_resilient as impl


async def click_number(page, target: int) -> bool:
    """Click the bottom-most visible exact page-number text, regardless of tag."""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(350)

    locator = page.get_by_text(str(target), exact=True)
    candidates = []
    for i in range(await locator.count()):
        item = locator.nth(i)
        try:
            if not await item.is_visible():
                continue
            box = await item.bounding_box()
            if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                candidates.append((box.get("y", 0), i))
        except Exception:
            continue

    # Pagination is near the bottom; choosing the lowest exact-number element
    # avoids unrelated values such as party size/date content higher on page.
    candidates.sort(reverse=True)
    for _, i in candidates:
        item = locator.nth(i)
        try:
            await item.scroll_into_view_if_needed()
            await item.click(force=True, timeout=3000)
            return True
        except Exception:
            # Some frameworks attach the handler to an ancestor rather than the
            # text span. A DOM click still bubbles to that ancestor.
            try:
                await item.evaluate("el => el.click()")
                return True
            except Exception:
                pass
    return False


async def click_next_fallback(page) -> bool:
    """Fallback for compact pagination windows with icon-only next controls."""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(300)

    # First reuse the original semantic next-control search.
    try:
        if await impl._original_click_next_fallback(page):
            return True
    except Exception:
        pass

    # Then inspect bottom-page SVG/icon controls. Click a candidate to the right
    # of the numeric pagination row; event bubbling handles nested SVG/span DOM.
    return bool(await page.evaluate(
        """() => {
          const visible = e => {
            const r=e.getBoundingClientRect(), s=getComputedStyle(e);
            return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
          };
          const all=[...document.querySelectorAll('body *')].filter(visible);
          const nums=all.filter(e=>/^\d+$/.test((e.textContent||'').trim()));
          if (!nums.length) return false;
          const bottom=Math.max(...nums.map(e=>e.getBoundingClientRect().top));
          const rowNums=nums.filter(e=>Math.abs(e.getBoundingClientRect().top-bottom)<55);
          if (!rowNums.length) return false;
          const maxRight=Math.max(...rowNums.map(e=>e.getBoundingClientRect().right));
          const row=all.filter(e=>{
            const r=e.getBoundingClientRect();
            if (Math.abs(r.top-bottom)>=65 || r.left < maxRight-2) return false;
            const txt=(e.textContent||'').trim();
            return txt==='' || txt==='›' || txt==='>' || /next|right|forward/i.test((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||''));
          }).sort((a,b)=>a.getBoundingClientRect().left-b.getBoundingClientRect().left);
          if (!row.length) return false;
          row[0].click();
          return true;
        }"""
    ))


# Preserve original next fallback so our replacement can call it.
impl._original_click_next_fallback = impl.click_next_fallback
impl.click_number = click_number
impl.click_next_fallback = click_next_fallback


if __name__ == "__main__":
    raise SystemExit(impl.main())
