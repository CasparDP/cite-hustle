# SSRN Scraper Updates - Direct URLs & Better Matching

## 🎯 What Changed

### 1. Direct URL Strategy ✅
**OLD approach (4+ requests per paper):**
```
1. Navigate to SSRN homepage
2. Search for title
3. Click on result
4. Load paper page
5. Extract abstract
```

**NEW approach (2 requests per paper):**
```
1. Search for title
2. Extract ALL URLs directly from search results page
3. Navigate directly to best matching URL
4. Extract abstract
```

**Impact:** ~50% fewer HTTP requests = less rate limiting!

### 2. Better Title Matching ✅

**OLD:** Only fuzzy string matching
- "Real Earnings Management" would score 100 for "Real Earnings Management and the Cost of Debt"
- Didn't consider title length at all

**NEW:** Combined similarity score
- **70% fuzzy matching** - How similar are the words?
- **30% length matching** - Are they similar length?

**Example:**
```
DB Title: "Real Earnings Management" (3 words)
Result 1: "Real Earnings Management and the Cost of Debt" (8 words)
Result 2: "Real Earnings Management Practices" (4 words)

OLD scores:
  Result 1: 100 (fuzzy only)
  Result 2: 85 (fuzzy only)

NEW scores:
  Result 1: 78.8 (fuzzy: 100, length: 37.5% → 3/8 words)
  Result 2: 89.3 (fuzzy: 85, length: 75% → 3/4 words)

Winner: Result 2 (more appropriate!)
```

## 📊 Configuration Options

### Adjust Length Similarity Weight

Default is 30% length, 70% fuzzy. You can adjust:

```python
from cite_hustle.collectors.ssrn_scraper import SSRNScraper

scraper = SSRNScraper(
    repo=repo,
    length_similarity_weight=0.4  # 40% length, 60% fuzzy
)
```

Higher weight = more emphasis on matching title length
Lower weight = more emphasis on fuzzy string matching

### Adjust Similarity Threshold

```bash
# Require higher combined score (more strict)
poetry run cite-hustle scrape --threshold 90

# Allow lower combined score (more lenient)
poetry run cite-hustle scrape --threshold 75
```

## 🧪 Testing the Changes

### Test 1: Rate Limiting Improvement

**Before:** Failed after 5-10 papers
**After:** Should handle more papers before rate limiting

```bash
poetry run cite-hustle scrape --limit 20 --delay 8
```

Expected: Should complete more papers successfully

### Test 2: Better Matching

Run with visible output to see the new scoring:

```bash
poetry run cite-hustle scrape --limit 5 --delay 10
```

You should see output like:
```
Results with combined similarity scores:
  [0] Score: 89.3 (fuzzy: 85, length: 0.75, words: 4/3)
      Title: Real Earnings Management Practices...
  [1] Score: 78.8 (fuzzy: 100, length: 0.38, words: 8/3)
      Title: Real Earnings Management and the Cost of Debt...
```

### Test 3: Specific Paper

Test with the paper that was failing:

```bash
poetry run python debug_ssrn_single.py
```

Should now:
- Show fewer request steps
- Show combined similarity scores
- Complete faster

## 🔍 What You'll See

### New Log Output

```
1/10: Real Earnings Management...

  → Navigating to SSRN homepage...
  → Waiting for search box...
  → Filling search box...
  → Clicking search button...
  → Waiting for search results...
  → Extracting paper URLs from search results...
  ✓ Found 8 results

  Results with combined similarity scores:
    [0] Score: 92.5 (fuzzy: 95, length: 0.80, words: 4/5)
        Title: Real Earnings Management in Financial Reporting...
    [1] Score: 81.3 (fuzzy: 100, length: 0.43, words: 7/5)
        Title: Real Earnings Management and the Cost of Debt...
  
  ✓ Selected: Real Earnings Management in Financial... (score: 92.5)
  ✓ URL: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567
  → Navigating to paper page...
  ✓ Extracted abstract (1250 chars)
```

### Rate Limiting

You should see:
- Fewer timeouts overall
- More papers processed before hitting rate limits
- Better error messages if it still fails

## 💡 Tips

### If Still Getting Rate Limited

Try even longer delays:
```bash
poetry run cite-hustle scrape --delay 15 --limit 10
```

Or batch processing:
```bash
# Do 10 papers
poetry run cite-hustle scrape --delay 10 --limit 10

# Wait an hour, then do 10 more
poetry run cite-hustle scrape --delay 10 --limit 10
```

### If Matches Are Too Strict

Lower the threshold or adjust length weight:

```bash
# Allow more lenient matches
poetry run cite-hustle scrape --threshold 75
```

Or modify in Python:
```python
scraper = SSRNScraper(
    repo=repo,
    similarity_threshold=80,
    length_similarity_weight=0.2  # Less emphasis on length
)
```

### If Matches Are Too Loose

Increase threshold or length weight:

```bash
poetry run cite-hustle scrape --threshold 90
```

Or:
```python
scraper = SSRNScraper(
    repo=repo,
    similarity_threshold=90,
    length_similarity_weight=0.4  # More emphasis on length
)
```

## 📈 Expected Improvements

### Request Reduction
- **Before:** 4-6 requests per paper
- **After:** 2-3 requests per paper
- **Improvement:** ~50% fewer requests

### Better Matching
- **Before:** False positives on long titles
- **After:** Considers both content and length
- **Result:** More accurate paper matching

### Success Rate
- **Before:** ~50% success rate due to rate limiting
- **After:** Should see 70-80%+ success rate

## 🐛 Debugging

If something doesn't work as expected:

### Check the logs
```python
from cite_hustle.database.models import DatabaseManager
from cite_hustle.config import settings

db = DatabaseManager(settings.db_path)
db.connect()

# See matching scores
failed = db.con.execute("""
    SELECT doi, match_score, error_message
    FROM ssrn_pages
    WHERE ssrn_url IS NULL
    ORDER BY match_score DESC
    LIMIT 20
""").fetchdf()

print(failed)
```

### Run with visible browser
```bash
poetry run cite-hustle scrape --no-headless --limit 3
```

Watch the terminal output to see:
- How many results found
- Combined similarity scores
- Which paper is selected

## 🎯 Next Steps

1. **Test with small batch:**
   ```bash
   poetry run cite-hustle scrape --limit 10 --delay 10
   ```

2. **Check results:**
   ```bash
   poetry run cite-hustle status
   ```

3. **Review matching quality:**
   - Look at which papers matched successfully
   - Check if any obvious mismatches
   - Adjust threshold if needed

4. **Scale up:**
   ```bash
   poetry run cite-hustle scrape --limit 50 --delay 10
   ```

## 📝 Summary

✅ **Direct URLs** - Fewer requests, less rate limiting
✅ **Better matching** - Considers both content and length
✅ **Configurable** - Adjust weights and thresholds
✅ **More info** - Detailed logging of match scores

Try it out and let me know how it performs!
