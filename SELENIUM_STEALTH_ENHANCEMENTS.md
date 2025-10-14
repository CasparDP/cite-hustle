# Selenium Stealth Enhancements for SSRN Scraper

## Summary

Enhanced the SSRN scraper (`src/cite_hustle/collectors/ssrn_scraper.py`) with comprehensive anti-detection measures based on industry best practices from BrowserStack and ScrapingAnt guides.

## Changes Implemented

### 1. User-Agent Rotation

**Added a pool of 8 realistic, diverse user-agents:**

- Chrome 119/120 on macOS, Windows, Linux
- Safari 17.1 on macOS
- Firefox 121 on macOS, Windows

**Implementation:**

- User-agent randomly selected at the start of each WebDriver session
- Includes desktop variants for Chrome, Safari, and Firefox
- All user-agents are modern (2023-2024) to avoid detection

### 2. Window Size Randomization

**Randomized viewport dimensions:**

- Width: 1920-2560 pixels (random)
- Height: 1080-1440 pixels (random)

**Benefit:** Prevents browser fingerprinting based on consistent window dimensions

### 3. Automation Flag Removal

**Critical Chrome options added:**

```python
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option('useAutomationExtension', False)
```

**Purpose:** Removes telltale signs that flag Selenium as an automation tool

### 4. CDP (Chrome DevTools Protocol) Commands

**Two critical CDP commands added:**

1. **User-Agent Override:**

   ```python
   driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": user_agent})
   ```

   Ensures user-agent is set at the network level, not just as a CLI argument

2. **WebDriver Property Removal:**
   ```python
   driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
   ```
   Removes the `navigator.webdriver` property that anti-bot systems check

### 5. Additional Stealth Options

**Added for comprehensive coverage:**

```python
chrome_options.add_argument("--disable-web-security")
chrome_options.add_argument("--allow-running-insecure-content")
```

### 6. Enhanced Logging

**Now logs:**

- Stealth mode status with truncated user-agent (for debugging)
- CDP command execution success/failure
- Clear indicators when stealth features are active

## Anti-Detection Strategy

The enhanced scraper now implements multiple layers of defense:

1. **Network Level:** CDP user-agent override
2. **Browser Level:** Automation flag removal, randomized dimensions
3. **JavaScript Level:** WebDriver property masking
4. **Session Level:** User-agent rotation per session
5. **Library Level:** selenium-stealth with realistic browser fingerprint

## Testing

To test the enhancements:

```bash
# Run with headless mode (production)
poetry run cite-hustle scrape --limit 5

# Run with visible browser (debugging)
poetry run cite-hustle scrape --limit 5 --no-headless
```

## Expected Improvements

1. **Reduced Cloudflare Challenges:** Anti-bot systems should trigger verification less frequently
2. **Higher Success Rate:** More papers successfully scraped without human verification
3. **Better Session Persistence:** Randomization makes it harder to fingerprint and block

## References

- BrowserStack Selenium Stealth Guide: https://www.browserstack.com/guide/selenium-stealth
- ScrapingAnt User-Agent Rotation: https://scrapingant.com/blog/change-user-agent-selenium

## Documentation Updates

Updated `.github/copilot-instructions.md` to reflect:

- User-agent rotation pool of 8 browser strings
- Window dimension randomization (1920-2560 x 1080-1440)
- Automation flag disabling
- CDP command usage for navigator.webdriver override

## Compatibility

- Python 3.12+
- selenium 4.x
- selenium-stealth 1.0.6+
- Chrome/ChromeDriver (headless mode supported)

## Notes

- The PDF downloader (`selenium_pdf_downloader.py`) already has similar stealth features
- Both scraper and downloader now use consistent anti-detection approaches
- Legacy HTTP downloader remains disabled due to Cloudflare protection
