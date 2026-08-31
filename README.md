# Screenshot Automation

Automates the daily capture of Qualtrics dashboard screenshots for the
internal AIQ application. Built with **Python + Playwright (Chromium)**.

Phase 1 (this project's current state) only covers **Spend Overview** for
the currently logged-in user. Phase 2 will add impersonation of Alex Belov
and Rachita Sundar, all six dashboards, and consolidated PDFs.

---

## 1. Prerequisites

- Python 3.10 or newer
- Windows PowerShell or `cmd.exe`
- Access to the AIQ web application in Chrome

## 2. Install (Windows)

From the project root:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 3. Configure the AIQ URL

Open `config.py` and set:

```python
AIQ_URL = "https://<your-real-aiq-url>/"
```

If Qualtrics has a stable direct URL you can also set `QUALTRICS_URL`,
otherwise the automation will click a "Qualtrics" entry in the AIQ nav.

## 4. Run Phase 1

```cmd
python main.py
```

What happens:

1. A Chromium window opens using the persistent profile at `.\browser_profile\`.
2. It navigates to AIQ.
3. If it looks like you are not logged in, the console will pause and print:

   ```
   ============================================================
     AIQ appears to require login.
     Please complete the login (including MFA/SSO) in the
     browser window that just opened.
     When AIQ is fully loaded, come back here and press ENTER.
   ============================================================
   ```

   Log in manually (including MFA/SSO). Come back to the terminal and
   press **ENTER**.
4. The automation navigates to Qualtrics, ensures the **Company** tab is
   selected, opens **Spend Overview**, and starts capturing screenshots
   while scrolling to the bottom.
5. Screenshots are saved to:

   ```
   output\test\General\01_Spend_Overview\
       001.png
       002.png
       ...
   ```

6. A run log is written to `output\test\run.log`.

## 5. Verify the screenshots

Open `output\test\General\01_Spend_Overview\` in File Explorer and
scroll through `001.png`, `002.png`, ... They should tile the full
dashboard from top to bottom, without large duplicated regions.

If you see problems, jump to **Troubleshooting** below.

## 6. Re-running

Subsequent runs will reuse the authenticated Chromium profile in
`.\browser_profile\`, so no manual login should be needed until the
session expires. When it does, the same manual-login prompt reappears.

---

## Project structure

```
screenshot_automation\
├── main.py                       Phase 1 entry point
├── config.py                     All tunable values / URLs / timings
├── requirements.txt
├── README.md
│
├── automation\
│   ├── __init__.py
│   ├── browser.py                Persistent Chromium context
│   ├── qualtrics.py              AIQ + Qualtrics navigation, Company tab
│   ├── screenshots.py            capture_entire_dashboard()
│   ├── selectors.py              ALL selectors live here
│   ├── impersonation.py          Phase 2 (stub)
│   └── verification.py           Phase 2 (stub)
│
├── output\                       Screenshots + logs (gitignore this)
└── browser_profile\              Persistent Chromium profile (gitignore this)
```

---

## How to fix a selector

If Phase 1 fails with a message like *"Could not find the 'Company' tab"*
or *"Could not find dashboard 'Spend Overview'"*, we need the real DOM
info for that element. Use Chrome DevTools:

1. In the AIQ browser window, right-click the element (e.g. the
   **Company** tab) and choose **Inspect**.
2. In DevTools, look at the highlighted node in the Elements panel.
3. Note two things:
   - Its **role** (usually visible as `role="tab"` / `role="button"` /
     `role="link"` on the element or an ancestor).
   - Its **visible/accessible name** (the text or `aria-label`).
4. Also right-click the node in DevTools and choose
   **Copy → Copy selector** to get a unique CSS selector as a fallback.

Then update `automation\selectors.py`:

- **Company tab not found** → paste the CSS selector into
  `COMPANY_TAB_CSS_FALLBACK`. If the visible text isn't literally
  `"Company"`, update `COMPANY_TAB_NAME`.
- **Company tab found but "not selected"** → open DevTools with the tab
  active and check what attribute or class marks it. Add the attribute
  (e.g. `("data-state", "active")`) to `COMPANY_TAB_SELECTED_ATTRS`, or
  the class name to `COMPANY_TAB_ACTIVE_CLASS_HINTS`.
- **Dashboard not found** → add a CSS selector under
  `DASHBOARD_CSS_FALLBACKS["Spend Overview"]`, or add extra visible
  labels to that dashboard's `ui_labels` in `config.py`.
- **Wrong element gets scrolled** → paste a CSS selector for the real
  scroll container into `DASHBOARD_SCROLL_CONTAINER`.

Copy back to me the following when you get stuck and I'll wire it in:

- Outer HTML of the element (right-click → **Copy → Copy outerHTML**).
- Its computed `role` and `aria-selected`/`aria-current` attributes.
- The CSS selector DevTools generates.

---

## Troubleshooting

**The automation loops forever / hits `MAX_SCROLLS`.**
Auto-detection picked a scroller that never reaches the bottom (or the
dashboard body has infinite scroll). Inspect the real scroll container
in DevTools (look for the element whose `scrollHeight > clientHeight`
around the dashboard body) and paste its CSS selector into
`selectors.DASHBOARD_SCROLL_CONTAINER`.

**Screenshots look identical.**
Either the scroll container isn't scrolling (see previous), or the
dashboard is short enough to fit in one viewport — in that case a single
`001.png` is expected.

**Login prompt appears every run.**
Something is wiping the profile. Confirm `browser_profile\` exists and
is writable, and that you're not running with `HEADLESS = True` (SSO
usually needs a real window).

**Nothing happens after clicking Qualtrics.**
Increase `DASHBOARD_LOAD_DELAY` in `config.py` or paste a selector for
the loading spinner into `selectors.DASHBOARD_LOADING_SPINNER` so we
can wait for it to disappear.

---

## Not yet implemented (Phase 2)

- Impersonation of Alex Belov (Ctrl+Shift+A → "Login As User")
- Switch-user to Rachita Sundar
- Post-impersonation `verify_current_user()`
- All six dashboards per user
- Per-user consolidated PDFs
- End-of-run PASS/FAIL summary across all users
