"""
Bepro ERP Match Slot Tracking & Auto-Registration Bot
Using Playwright with persistent browser context / user session.

Features:
- Tracks slot availability in real time.
- Prioritizes 'Mode 1.0' over 'Mode 0.5'.
- Automatically selects slot and clicks Submit button.
- Waits until response is received from website upon submission.
- Respects 60-second cooldown period post-registration ("Wait ...s").
- Immediately reloads page and retries upon registration failure.
"""

import asyncio
import argparse
import sys
import os
import re
import time
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError

sys.stdout.reconfigure(encoding='utf-8')

# Colors for Terminal Logs
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def log(msg: str, level: str = 'info'):
    timestamp = datetime.now().strftime('%H:%M:%S')
    color = {
        'info': Colors.CYAN,
        'success': Colors.GREEN,
        'warn': Colors.WARNING,
        'error': Colors.FAIL,
        'header': Colors.HEADER
    }.get(level, Colors.CYAN)
    print(f"{color}[{timestamp}] {msg}{Colors.ENDC}", flush=True)

TARGET_URL = "https://bepro-erp.web.app/analysis/editor/assignment-request?viewType=tracking"

class BeproBot:
    def __init__(self, page: Page, mode_priority: str = "1.0_FIRST"):
        self.page = page
        self.mode_priority = mode_priority # '1.0_FIRST', '1.0_ONLY', '0.5_ONLY', 'ANY'
        self.total_success = 0
        self.total_fail = 0

        # --- DANH SÁCH ƯU TIÊN THEO NGÀY VÀ GIỜ ---
        # Cấu hình danh sách ở đây. Chuỗi khớp với nội dung trên bảng.
        # Ưu tiên từ trên xuống dưới (trái sang phải).
        self.date_priority = ['10/11', '10/12', '10/13']
        self.time_priority = ['19:00 ~ 22:00', '15:00 ~ 18:00', '11:00 ~ 14:00', '07:00 ~ 10:00']

    def sort_slots(self, slots):
        """Sắp xếp các slot dựa trên độ ưu tiên ngày và giờ"""
        def get_score(slot):
            date_score = 999
            for i, p_date in enumerate(self.date_priority):
                if p_date in slot.get('date', ''):
                    date_score = i
                    break
            
            time_score = 999
            for i, p_time in enumerate(self.time_priority):
                if p_time in slot.get('time', ''):
                    time_score = i
                    break
            return (date_score, time_score)

        return sorted(slots, key=get_score)

    async def get_cooldown_remaining(self) -> int:
        """Checks if the submit button displays 'Wait Xs' cooldown text."""
        try:
            btn_text = await self.page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const b of buttons) {
                    const txt = (b.innerText || b.textContent || '').trim();
                    if (/Wait\\s+\\d+\\s*s/i.test(txt)) return txt;
                }
                return null;
            }''')
            if btn_text:
                import re
                m = re.search(r'Wait\s+(\d+)\s*s', btn_text, re.IGNORECASE)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return 0

    async def scan_and_click_fastest_slot(self):
        """Tối ưu hóa tốc độ: Quét, chấm điểm ưu tiên và click ngay lập tức bằng Javascript tại Browser"""
        js_code = f'''() => {{
            const datePriority = {self.date_priority};
            const timePriority = {self.time_priority};
            const modePriority = "{self.mode_priority}";
            
            const buttons = Array.from(document.querySelectorAll('tbody tr td button, .table button'));
            let bestSlot = null;
            let bestScore = [999, 999, 999]; // [mode, date, time]
            
            for (let index = 0; index < buttons.length; index++) {{
                const btn = buttons[index];
                const isDisabled = btn.disabled || btn.getAttribute('disabled') !== null || btn.getAttribute('aria-disabled') === 'true';
                if (isDisabled) continue;

                const style = window.getComputedStyle(btn);
                if (style.cursor === 'not-allowed' || parseFloat(style.opacity) < 0.8) continue;

                const text = (btn.innerText || btn.textContent || '').trim();
                if (text.includes('Clear') || text.includes('Submit') || text.includes('Wait')) continue;
                
                let isMode1 = text.includes('Mode 1.0') || text === '1.0' || text === '1';
                let isMode05 = text.includes('Mode 0.5') || text === '0.5';
                
                let modeScore = 999;
                if (modePriority === '1.0_FIRST') {{
                    if (isMode1) modeScore = 1; else if (isMode05) modeScore = 2;
                }} else if (modePriority === '1.0_ONLY') {{
                    if (isMode1) modeScore = 1; else continue;
                }} else if (modePriority === '0.5_ONLY') {{
                    if (isMode05) modeScore = 1; else continue;
                }} else {{
                    if (isMode1) modeScore = 1; else if (isMode05) modeScore = 2; else modeScore = 3;
                }}
                
                if (modeScore === 999) continue;

                let timeText = ''; let dateText = '';
                const td = btn.closest('td'); const tr = btn.closest('tr');
                if (tr && td) {{
                    const firstTd = tr.querySelector('td');
                    if (firstTd) timeText = (firstTd.innerText || firstTd.textContent || '').trim();
                    const table = tr.closest('table');
                    if (table) {{
                        const ths = table.querySelectorAll('thead tr th, thead tr td, tbody tr:first-child td');
                        if (ths[td.cellIndex]) dateText = (ths[td.cellIndex].innerText || ths[td.cellIndex].textContent || '').trim();
                    }}
                }}
                
                let dateScore = 999;
                for (let i = 0; i < datePriority.length; i++) {{
                    if (dateText.includes(datePriority[i])) {{ dateScore = i; break; }}
                }}
                let timeScore = 999;
                for (let i = 0; i < timePriority.length; i++) {{
                    if (timeText.includes(timePriority[i])) {{ timeScore = i; break; }}
                }}
                
                if (modeScore < bestScore[0] || 
                   (modeScore === bestScore[0] && dateScore < bestScore[1]) || 
                   (modeScore === bestScore[0] && dateScore === bestScore[1] && timeScore < bestScore[2])) {{
                    bestScore = [modeScore, dateScore, timeScore];
                    bestSlot = {{ btn, index, text, timeText, dateText }};
                }}
            }}
            
            if (bestSlot) {{
                bestSlot.btn.click(); // CLICK NGAY LẬP TỨC
                return {{ text: bestSlot.text, time: bestSlot.timeText, date: bestSlot.dateText }};
            }}
            return null;
        }}'''
        return await self.page.evaluate(js_code)

    async def click_submit_and_monitor(self, slot_desc: str):
        """Clicks Submit button and waits until server response (success/fail toast or cooldown)."""
        log(f"Submitting match registration for [{slot_desc}]...", 'info')

        # Click the submit button
        submit_clicked = await self.page.evaluate('''() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim();
                if (/^Submit$/i.test(txt)) {
                    b.click();
                    return true;
                }
            }
            return false;
        }''')

        if not submit_clicked:
            log("Submit button was not found or not clickable.", 'warn')
            return False

        log("Submit clicked. Waiting for server response...", 'info')

        while True:
            # Check for Success Toast, Active Cooldown, or Failure Toast
            result = await self.page.evaluate('''() => {
                const toastElements = Array.from(document.querySelectorAll('[role="alert"], [role="dialog"], [class*="toast"], [class*="message"], [class*="notification"], [class*="modal"], .ant-message-notice, .ant-notification-notice, .ant-modal-content'));
                const allTexts = toastElements.map(el => (el.innerText || el.textContent || '').trim()).join(' ');
                const bodyText = (document.body.innerText || '') + ' ' + allTexts;

                if (bodyText.includes('Application submitted') || bodyText.includes('assigned')) {
                    return { status: 'SUCCESS', msg: 'Application submitted' };
                }
                if (
                    bodyText.includes('Assignment failed') || 
                    bodyText.includes('Failed to submit') ||
                    bodyText.includes('No matches available') ||
                    bodyText.includes('No weekday apply credits') ||
                    bodyText.includes('cannot apply to the selected view') ||
                    bodyText.includes('failedDetail')
                ) {
                    return { status: 'FAIL', msg: 'Assignment failed' };
                }
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const b of buttons) {
                    const txt = (b.innerText || b.textContent || '').trim();
                    if (/Wait\\s+\\d+\\s*s/i.test(txt)) {
                        return { status: 'SUCCESS_COOLDOWN', msg: txt };
                    }
                }
                return null;
            }''')

            if result:
                if result['status'] in ('SUCCESS', 'SUCCESS_COOLDOWN'):
                    self.total_success += 1
                    log(f"🎉 MATCH REGISTERED SUCCESSFULLY! ({result['msg']})", 'success')
                    log("Entering 60-second cooldown period...", 'info')
                    # Wait through the cooldown
                    while True:
                        cd = await self.get_cooldown_remaining()
                        if cd <= 0:
                            break
                        log(f"Cooldown remaining: {cd}s...", 'info')
                        await asyncio.sleep(2)
                    log("Cooldown complete! Resuming slot scanning...", 'info')
                    return True
                elif result['status'] == 'FAIL':
                    self.total_fail += 1
                    log(f"❌ Registration failed ({result['msg']}). Reloading page to retry...", 'error')
                    await self.page.reload()
                    await self.page.wait_for_load_state('domcontentloaded')
                    await asyncio.sleep(5)
                    await self.click_pre_tracking_button(self.page)
                    return False

            await asyncio.sleep(0.3)

    async def click_pre_tracking_button(self, page):
        """
        Click the button required before entering tracking loop.
        """
        print("Looking for Tracking control...")
        name_pattern = re.compile(r"^tracking$", re.IGNORECASE)
        candidates = (
            page.get_by_role("button", name=name_pattern).first,
            page.get_by_role("tab", name=name_pattern).first,
            page.get_by_text(name_pattern).first,
            page.locator(
                'button, [role="button"], [role="tab"], a'
            ).filter(has_text=name_pattern).first,
        )

        for candidate in candidates:
            try:
                await candidate.wait_for(state="visible", timeout=3000)
                await candidate.click()
                print("✅ Tracking control clicked")
                await asyncio.sleep(1)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        # After the first login, the control may already have been activated.
        try:
            await page.locator("tbody, .table").first.wait_for(
                state="visible", timeout=5000
            )
            print("✅ Tracking view is already active")
            return True
        except PlaywrightTimeoutError:
            visible_controls = await page.locator(
                'button:visible, [role="button"]:visible, '
                '[role="tab"]:visible, a:visible'
            ).all_inner_texts()
            print(
                "⚠️ Tracking control not found. "
                f"Visible controls: {visible_controls[:20]}"
            )
            return False

    async def login(self,page):
        """
        Automatically login with email and password.
        """
        EMAIL = 'tutuanvi2001@gmail.com'
        #EMAIL = 'huynhkimxuan05@gmail.com'
        
        PASSWORD = '4102001tuanvi'
        #PASSWORD = 'Bepro@2026'
        try:
            print("Attempting auto-login...")
            
            # Fill email
            email_input = page.locator('//*[@id="email"]')
            if await email_input.is_visible():
                await email_input.fill(EMAIL)
                print("✅ Email entered")
            else:
                print("⚠️ Email field not found")
                return False
            
            await asyncio.sleep(0.5)
            
            # Fill password
            password_input = page.locator('//*[@id="password"]')
            if await password_input.is_visible():
                await password_input.fill(PASSWORD)
                print("✅ Password entered")
            else:
                print("⚠️ Password field not found")
                return False
            
            await asyncio.sleep(0.5)
            
            # Click login button
            login_button = page.locator('//*[@id="root"]/div/div/div/div/div/form/div[2]/button')
            if await login_button.is_visible():
                await login_button.click()
                print("✅ Login button clicked")
                await page.wait_for_load_state("networkidle")
                print("✅ Login successful!")
                return True
            else:
                print("⚠️ Login button not found")
                return False
            
        except Exception as e:
            print(f"❌ Login failed: {e}")
            return False

    async def run(self):
        log(f"Navigating to Bepro ERP: {TARGET_URL}", 'header')
        await self.page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(1)
        if not await self.login(self.page):
            log("Login failed; stopping bot.", 'error')
            return
        await asyncio.sleep(1)
        await self.click_pre_tracking_button(self.page)

        log("Waiting for Bepro ERP table to load...", 'info')
        # Wait until table or login page appears
        try:
            await self.page.wait_for_selector('tbody, .table, form, input[type="email"]', timeout=15000)
        except Exception:
            pass

        log(f"Bot started! Scanning interval active (Mode Preference: {self.mode_priority}).", 'success')

        while True:
            try:
                # Check if in cooldown
                cooldown = await self.get_cooldown_remaining()
                if cooldown > 0:
                    log(f"Waiting for active cooldown: {cooldown}s", 'info')
                    await asyncio.sleep(1.5)
                    continue

                # Quét và click trực tiếp ở Browser (0 ms latency)
                target_slot = await self.scan_and_click_fastest_slot()

                if target_slot:
                    log(f"⚡ FAST CLICK: Đã tự động chọn slot [{target_slot['text']}] at {target_slot.get('date')} {target_slot.get('time')}", 'success')
                    await asyncio.sleep(0.01)
                    await self.click_submit_and_monitor(target_slot['text'])

            except Exception as e:
                log(f"Scan loop exception: {e}", 'error')

            # Chờ 0s để nhường luồng (event loop), tốc độ quét tối đa
            await asyncio.sleep(0.2)

async def main():
    parser = argparse.ArgumentParser(description="Bepro ERP Slot Tracking Bot")
    parser.add_argument("--mode", choices=["1.0_FIRST", "1.0_ONLY", "0.5_ONLY", "ANY"], default="1.0_FIRST", help="Slot mode priority")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()

    log("="*50, 'header')
    log("  BEPRO ERP MATCH SLOT TRACKER & AUTO-REGISTER BOT", 'header')
    log("="*50, 'header')

    headless = args.headless or not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    if headless and not args.headless:
        log("No graphical display detected; using headless mode.", 'warn')

    async with async_playwright() as p:
        log("Launching temporary Chrome session (no user_data saved).", 'info')
        browser = await p.chromium.launch(
            headless=headless,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )
        context = await browser.new_context(viewport=None)

        page = await context.new_page()
        bot = BeproBot(page=page, mode_priority=args.mode)
        await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bot stopped by user.", 'warn')
