# IMMEDIATE NEXT STEPS - SSRN Debugging

## What You Just Saw

The error message now shows:
```
✗ Error searching SSRN for 'Thirty Years of Change...': Timeout waiting for page elements: Message:
```

This means the scraper is timing out while waiting for an element on the page. **The new code will now show you WHICH step is failing.**

## CRITICAL: Run This Now

### Step 1: Test with Visual Browser

Run the debug script I just created:

```bash
poetry run python debug_ssrn_single.py
```

This will:
- Open Chrome browser **visibly** (not headless)
- Try to search for the exact paper that's failing
- Show you step-by-step what's happening
- Save the HTML page if it fails
- **You'll be able to SEE what SSRN is showing**

### Step 2: What to Look For

When the browser opens, check:

1. **CAPTCHA?** - Does SSRN show a CAPTCHA challenge?
2. **Rate Limit Message?** - Does it say "Too many requests" or similar?
3. **Different Page?** - Is it showing a different layout than expected?
4. **Search Box Missing?** - Is the search box not appearing?

### Step 3: Based on What You See

#### If you see CAPTCHA:
```
→ You're being blocked by SSRN's anti-bot protection
→ Solution: Use direct SSRN URLs instead of searching
→ Or: Add longer delays (15-30 seconds between papers)
```

#### If you see "Rate Limit" or "Too Many Requests":
```
→ You've hit SSRN's rate limit
→ Solution: Increase --delay to 30+ seconds
→ Or: Process in small batches (5 papers, wait 1 hour, repeat)
```

#### If page structure looks different:
```
→ SSRN changed their HTML
→ Solution: Update CSS selectors in the code
→ I can help with this once we see the actual HTML
```

#### If search box appears but times out later:
```
→ Something slow on their end or network issue
→ Solution: Increase timeout from 10 to 30 seconds
```

## Expected Output from Debug Script

With the updated code, you should now see:

```
Testing SSRN scraper with title: Thirty Years of Change...
Running in NON-HEADLESS mode so you can see what's happening...
================================================================================

Setting up Chrome browser (you should see it open)...

Attempting to search SSRN...
  → Navigating to SSRN homepage...
  → Waiting for search box...
  [THIS IS WHERE IT'S LIKELY FAILING - you'll see which step]
```

## Quick Diagnosis

Based on the step where it stops:

**Stops at "Waiting for search box":**
- Page isn't loading
- CAPTCHA is blocking
- Different page structure

**Stops at "Clicking search button":**
- Button selector changed
- Page loaded differently

**Stops at "Waiting for search results":**
- Search didn't work
- Results page blocked
- Rate limited

## Alternative: Check Screenshots

The updated code saves screenshots on error. After running the regular scrape command, check:

```bash
ls -lth ~/Dropbox/Github\ Data/cite-hustle/ssrn_html/ERROR_*.png | head -5
```

Open the most recent one:
```bash
open ~/Dropbox/Github\ Data/cite-hustle/ssrn_html/ERROR_Thirty_Years_*.png
```

## Better Approach: Direct URLs

If SSRN is blocking search, consider this alternative:

### Option A: Search SSRN Manually First
1. Go to SSRN.com
2. Search for paper title
3. Get the paper ID (e.g., `12345678`)
4. Use direct URL: `https://papers.ssrn.com/sol3/papers.cfm?abstract_id=12345678`

### Option B: Use Title + Author Search
Instead of searching SSRN's main search, try:
- SSRN Advanced Search API (if available)
- Google Search: `site:ssrn.com "paper title"`
- Scholar APIs that include SSRN

## Configuration to Try

### For Rate Limiting:
```bash
# Much longer delays
poetry run cite-hustle scrape --delay 30 --limit 5
```

### For Timeout Issues:
Edit `ssrn_scraper.py` line ~111:
```python
# Change timeout from 10 to 30
def search_ssrn(self, title: str, timeout: int = 30):
```

## Files to Check

After the debug script runs:

1. **Screenshot**: `~/Dropbox/Github Data/cite-hustle/ssrn_html/ERROR_*.png`
2. **HTML**: `~/Dropbox/Github Data/cite-hustle/ssrn_html/debug_failed_page.html`
3. **Terminal output**: Will show which step failed

## Report Back

After running `debug_ssrn_single.py`, tell me:

1. **Which step did it fail at?** (from the terminal output)
2. **What does the browser show?** (CAPTCHA, rate limit, normal page?)
3. **What's in the screenshot?**
4. **Current URL shown in terminal?**

With that info, I can give you the exact fix!

## Quick Win: If This Paper Exists on SSRN

You said the paper exists. Try this manually:

1. Go to https://www.ssrn.com
2. Search: "Thirty Years of Change: The Evolution of Classified Boards"
3. Find the paper
4. Copy the URL
5. Share it with me

If we can see the pattern of the URL, we might be able to skip the search entirely and construct URLs directly!
