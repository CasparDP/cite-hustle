# Quick Start: Testing Your Improved SSRN Scraper

## Pre-Test Checklist

- [ ] Syntax verified: `poetry run python -m py_compile src/cite_hustle/collectors/ssrn_scraper.py`
- [ ] All 4 documentation files created:
  - [ ] `SSRN_SCRAPER_CRITICAL_REVIEW.md`
  - [ ] `SSRN_SCRAPER_IMPLEMENTATION_GUIDE.md`
  - [ ] `SSRN_DETECTION_DECISION_TREE.md`
  - [ ] `SSRN_SCRAPER_CODE_REVIEW_SUMMARY.md`

---

## Test Plan (Run in Order)

### Test 1: Verify Variable Delays (5-10 minutes)

**Goal:** Confirm delays are random, not fixed 30s

```bash
cd /Users/casparm4/Github/cite-hustle
poetry run cite-hustle scrape --limit 5 --no-headless
```

**Expected Output:**

```
1/5: Paper Title...
→ Navigating to SSRN homepage...
⏳ Next article in 23.5s...  ← Should vary

2/5: Another Paper...
→ Navigating to SSRN homepage...
    [Human pause] Adding 62.1s extra delay (user distraction simulation)  ← Occasional extra pauses
⏳ Next article in 89.3s...  ← Different time

3/5: Next Paper...
...
```

**Pass Criteria:**

- [ ] Delays vary (not all 30s)
- [ ] Some runs have `[Human pause]` message (occasional 30-120s extra)
- [ ] No timeouts or errors

**If it fails:**

- Check terminal for Python errors (should have none)
- Review: Are you getting to the paper page or failing on search?

---

### Test 2: Monitor for Cloudflare Challenge (20-30 minutes)

**Goal:** See if Cloudflare challenge appears and how code handles it

```bash
poetry run cite-hustle scrape --limit 15 --no-headless
```

**Expected Scenarios:**

#### Scenario A: No Cloudflare Challenge (Lucky!)

```
1/15: Paper...
→ Navigating to SSRN homepage...
→ Navigating (attempt 1/3)...  ← No challenge detected
→ Waiting for search box...
...
✓ Selected: [Title] (score: 87.3)
✓ URL: https://ssrn.com/abstract=12345
✓ Extracted abstract (423 chars)
```

**Pass:** If you get through all 15 papers with no challenges, you're golden.

#### Scenario B: Cloudflare Challenge Detected (Normal)

```
1/5: Paper...
→ Navigating to SSRN homepage...
⚠️  Cloudflare challenge detected!  ← Detection working!
⏳ Waiting for Cloudflare clearance (up to 15s)...
✓ Cloudflare cookie acquired! Challenge passed.  ← Handler working!
→ Waiting for search box...
...
```

**Pass:** If you see challenge detected AND cookie acquired, improvements are working.

#### Scenario C: Challenge Timeout (Still OK)

```
⚠️  Cloudflare challenge detected!
⏳ Waiting for Cloudflare clearance (up to 15s)...
✗ Cloudflare clearance timeout after 15s
⏳ Retry in 5s...  ← Exponential backoff
→ Navigating (attempt 2/3)...
(Either succeeds on retry or fails again)
```

**Pass:** You see retries happening (means code is handling failures).

#### Scenario D: IP Blocked (Bad - Skip This Test)

```
✗ IP blocked or rate limited (403/429 response)
✗ IP appears to be blocked. Consider using a proxy.
```

**Action:** Wait 1 hour for IP block to expire, then retry.

---

### Test 3: Check Success Rate (Track Over Time)

**Goal:** Confirm your success rate is improving

After running 3-5 scrape batches, check your DB:

```bash
# SSH into your DB or connect locally
sqlite3 ~/Dropbox/Github\ Data/cite-hustle/DB/articles.duckdb << 'EOF'
SELECT
  COUNT(*) as total,
  COUNT(CASE WHEN ssrn_url IS NOT NULL THEN 1 END) as success,
  ROUND(100.0 * COUNT(CASE WHEN ssrn_url IS NOT NULL THEN 1 END) / COUNT(*), 1) as success_rate
FROM ssrn_pages
WHERE created_at > datetime('now', '-1 day');
EOF
```

**Expected:**

```
total | success | success_rate
------|---------|-------------
50    | 35      | 70.0
```

**Baseline (before today's changes):** ~50-60% success rate  
**Expected (after today's changes):** ~65-75% success rate

**Track this weekly:**

- Week 1 after changes: 65-70%
- Week 2: 68-75%
- Beyond: Should stabilize at 70-80% (unless IP gets flagged)

---

## Monitoring Commands (Run After Each Batch)

### Success Rate by Day

```bash
sqlite3 ~/Dropbox/Github\ Data/cite-hustle/DB/articles.duckdb << 'EOF'
SELECT
  DATE(created_at) as date,
  COUNT(*) as total,
  COUNT(CASE WHEN ssrn_url IS NOT NULL THEN 1 END) as success,
  ROUND(100.0 * COUNT(CASE WHEN ssrn_url IS NOT NULL THEN 1 END) / COUNT(*), 1) as rate
FROM ssrn_pages
WHERE created_at > datetime('now', '-14 days')
GROUP BY DATE(created_at)
ORDER BY date DESC;
EOF
```

### Error Distribution

```bash
sqlite3 ~/Dropbox/Github\ Data/cite-hustle/DB/articles.duckdb << 'EOF'
SELECT
  CASE
    WHEN error_message LIKE '%Cloudflare%' THEN 'Cloudflare'
    WHEN error_message LIKE '%blocked%' THEN 'IP Blocked'
    WHEN error_message LIKE '%Timeout%' THEN 'Timeout'
    WHEN ssrn_url IS NOT NULL THEN 'Success'
    ELSE 'Other'
  END as error_type,
  COUNT(*) as count
FROM ssrn_pages
WHERE created_at > datetime('now', '-7 days')
GROUP BY error_type
ORDER BY count DESC;
EOF
```

### Average Match Quality

```bash
sqlite3 ~/Dropbox/Github\ Data/cite-hustle/DB/articles.duckdb << 'EOF'
SELECT
  ROUND(AVG(match_score), 1) as avg_score,
  MIN(match_score) as min_score,
  MAX(match_score) as max_score,
  COUNT(*) as count
FROM ssrn_pages
WHERE created_at > datetime('now', '-7 days')
  AND ssrn_url IS NOT NULL;
EOF
```

---

## Tuning Guide (If Things Aren't Working)

### Problem: Still Getting ~50% Success Rate

**Diagnosis:** IP likely flagged, or Cloudflare challenge wait isn't working

**Try:**

1. Increase delay: Change `crawl_delay=35` → `crawl_delay=60`
2. Check if Cloudflare challenge appears: Run with `--no-headless`
3. If challenge appears but times out: IP is blocked, wait 1 hour

### Problem: "Cloudflare clearance timeout after 15s"

**Diagnosis:** Challenge JS is running but taking > 15 seconds

**Try:**

1. Increase wait timeout: In code, change `timeout=15` → `timeout=25`
2. Run one test with `--no-headless` to see actual challenge page
3. Check network latency (if slow, increase timeout)

### Problem: Lots of "Timeout waiting for page elements"

**Diagnosis:** SSRN or Cloudflare is slow, or challenge not being detected

**Try:**

1. Increase timeout: In code, change `timeout=10` → `timeout=15`
2. Add more delay: Change `crawl_delay=35` → `crawl_delay=50`
3. Check internet speed (too slow = real bottleneck, not code)

### Problem: "No match above threshold" (score < 85)

**Diagnosis:** Your title matching is strict, or SSRN results don't match

**Try:**

1. Lower threshold: Change `similarity_threshold=85` → `similarity_threshold=80`
2. Adjust weights: In code, change `length_similarity_weight=0.3` → `0.4`
3. Check a failing paper manually on SSRN.com (is it actually there?)

---

## Rollback Plan (If Something Breaks)

If new code causes problems:

```bash
# Revert to old version
git checkout HEAD~1 src/cite_hustle/collectors/ssrn_scraper.py

# Or check what changed
git diff HEAD src/cite_hustle/collectors/ssrn_scraper.py
```

**Note:** All changes are **backward compatible**. Old code will still work (just uses fixed delays instead of variable).

---

## Next Milestone: Proxy Integration

Once you've confirmed 65-75% success rate and want to push to 85-95%:

1. **Evaluate proxy services** (1 week research)

   - Bright Data: $100+/month, but 99% reliable
   - Oxylabs: $80+/month, fast
   - ScraperAPI: $50/month, slower but budget-friendly

2. **Prototype integration** (1-2 weeks coding)

   - Add `--proxy-service` CLI option
   - Rotate proxy IPs in `setup_webdriver()`
   - Test with small batch first

3. **Monitor improvement** (1 week testing)
   - Track success rate with proxy on
   - Compare to no-proxy baseline
   - Calculate ROI: Cost vs time saved

---

## Success Criteria (When to Stop Tuning)

✅ **You're done when:**

- [ ] Delays are variable (not fixed 30s)
- [ ] Cloudflare challenges are detected and handled
- [ ] Success rate is 70%+ (up from baseline 50-60%)
- [ ] No new Python errors
- [ ] Code is pushing to production/production-like environment

❌ **You need proxy if:**

- [ ] Success rate capped at 70-75% (can't improve further)
- [ ] Running high-volume scrapes (100+ papers regularly)
- [ ] IP blocks happening frequently (every 30-50 papers)

---

## Documentation Quick Links

- **Want details on all weaknesses?** → `SSRN_SCRAPER_CRITICAL_REVIEW.md`
- **How do I test each change?** → `SSRN_SCRAPER_IMPLEMENTATION_GUIDE.md`
- **Am I over-detected?** → `SSRN_DETECTION_DECISION_TREE.md`
- **What changed in my code?** → `SSRN_SCRAPER_CODE_REVIEW_SUMMARY.md`

---

## Timeline

**Week 1 (NOW):**

- [ ] Run Test 1, 2, 3 above
- [ ] Verify improvements
- [ ] Monitor success rate

**Week 2:**

- [ ] Decide on proxy investment
- [ ] If yes: Start proxy evaluation
- [ ] If no: Optimize current code (session persistence, etc.)

**Week 3+:**

- [ ] If proxy: Implement integration
- [ ] Monitor final success rate
- [ ] Deploy to production

---

## Questions? Debug Checklist

1. **Code won't run?**

   - [ ] Syntax error: `poetry run python -m py_compile src/cite_hustle/collectors/ssrn_scraper.py`
   - [ ] Missing imports: Check top of file (all should be there)

2. **Still getting Cloudflare 100% of the time?**

   - [ ] Your IP might be pre-flagged (use VPN to test)
   - [ ] Run `--no-headless` to see actual page
   - [ ] Check browser console for JS errors

3. **Success rate didn't improve?**

   - [ ] Make sure you're running NEW code (check file timestamp)
   - [ ] Compare DB records with old vs new timestamps
   - [ ] Check: Are you using the new `scrape` command? (vs old path)

4. **Timeouts happening everywhere?**
   - [ ] Network latency issue: test SSRN.com manually
   - [ ] Or Cloudflare challenge: check with `--no-headless`
   - [ ] Increase timeout to 20s as diagnostic
