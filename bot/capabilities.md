# Ads Bot capabilities

`yes` = answerable now from the loaded tables with defaults. `partial` = answerable but part of the answer needs a setting or hits a known data gap (the bot says which). `no` = data not loaded.

Settings listed are only needed for the part marked; a null setting returns `needs setting: <key>` for that part.

| # | Question | Now? | Data needed | Settings needed | If a setting or data is missing |
|---|---|---|---|---|---|
| 1 | Which search query of the keyword had high impressions and higher budg... | yes | search_query | contribution_before_ads_pct_of_mrp_gmv, min_data, waste_rule | - |
| 2 | Which city is consuming more budget? | yes | granular | - | - |
| 3 | Which city is giving me the highest ads revenue? | yes | granular | - | - |
| 4 | Which SKU × city × keyword is working best? Where is there scope for i... | yes | granular | contribution_before_ads_pct_of_mrp_gmv, min_data; product_title_to_sku for short names | - |
| 5 | What changes should I make this week? | partial | granular, search_query; stock not loaded | waste_rule, scale_rule, min_data, contribution_before_ads_pct_of_mrp_gmv, max_weekly_actions, lag_days | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 6 | What is your analysis on this? | partial | granular, search_query; stock not loaded | waste_rule, scale_rule, min_data, contribution_before_ads_pct_of_mrp_gmv, max_weekly_actions, lag_days | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 7 | Which campaigns should I increase budgets on? | partial | granular; stock not loaded | contribution_before_ads_pct_of_mrp_gmv, scale_rule, budget_increase_step_pct | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 8 | What is my budget split across attack (competitor keywords), expand (c... | yes | search_query, granular | split_target (for gap); defend_terms, attack_terms (optional) | - |
| 9 | Which keywords should I change match type on? | yes | search_query | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 10 | Based on the type of keywords working for me, suggest more for a speci... | yes | search_query | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 11 | Should I start a city-wise campaign for any specific SKU? | partial | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 12 | Give me a one-line verdict on Instamart ads for the period I just uplo... | partial | granular | lag_days, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 13 | How did spend, impressions, clicks, GMV and ROAS change versus last we... | yes | granular | - | - |
| 14 | Which 3 things changed the most this week, and why? | yes | granular | - | - |
| 15 | Is ROAS falling because of cost, click-through, conversion or attribut... | partial | granular | lag_days | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 16 | Which days in this period are still collecting attributed sales and sh... | partial | granular (direct 7d vs 14d) | lag_days | Shows days where direct 14d GMV equals 7d as a data-observed fact; judgement still says needs setting: lag_days |
| 17 | What % of total spend went to placements without a SKU (Collection Ads... | yes | granular | - | - |
| 18 | What is A2S for this period, and is it above my ceiling? | partial | granular; sales figure comes from settings | a2s_sales_basis, a2s_ceiling_pct | Shows spend only; A2S says needs setting: a2s_sales_basis |
| 19 | Which campaigns spent the most with the least to show for it? | yes | granular | - | - |
| 20 | Which campaigns improved the most versus last week? | yes | granular | - | - |
| 21 | If I can only do one thing today, what should it be? | partial | granular, search_query; stock not loaded | waste_rule, min_data | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 22 | Which search queries spent the most with ROAS below 1? | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 23 | Which search queries spent money but got zero GMV? | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 24 | List the top 20 wasteful search queries with spend, clicks, ROAS and t... | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 25 | Which irrelevant searches am I paying for? | partial | search_query | sku_keywords (else only keyword-contains check) | Falls back to 'query does not contain the keyword' and labels it a weaker test |
| 26 | Which searches have high click-through but poor conversion? | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 27 | Which searches convert well but get little spend, so deserve more budg... | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 28 | What new search queries appeared this week, and how are they doing? | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 29 | Group my search queries by theme (chips, khakhra, biscuits and dips, p... | yes | search_query | - | - |
| 30 | Which spelling variants should be combined, and what do they look like... | yes | search_query | - | - |
| 31 | What should my negative keyword list be this week? | partial | search_query | waste_rule, min_data | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 32 | Which search queries should get their own exact-match keyword? | yes | search_query | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 33 | How much spend would I save by cutting every query below my waste thre... | yes | search_query | min_data, waste_rule, contribution_before_ads_pct_of_mrp_gmv | - |
| 34 | How do broad and exact match compare on spend, CPC, CPM and ROAS? | yes | granular, search_query | min_data | - |
| 35 | Which broad keywords are spilling into irrelevant searches? | yes | search_query | min_data | - |
| 36 | What searches does broad match on "snackible" actually serve on, and w... | yes | search_query | min_data | - |
| 37 | Which broad keywords serve competitor searches by accident? | yes | search_query | min_data | - |
| 38 | For "healthy snacks", how do exact and broad compare, and should I shi... | yes | search_query | min_data | - |
| 39 | Which keywords should move from broad to exact? | yes | search_query | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 40 | Which exact keywords are too narrow and getting too few impressions? | yes | granular, search_query | min_data | - |
| 41 | Where are exact and broad versions of the same keyword competing with... | yes | granular, search_query | min_data | - |
| 42 | How much of "choki choki" broad spend landed on other searches like "w... | yes | search_query | min_data | - |
| 43 | What share of each keyword's spend lands on searches that don't contai... | yes | search_query | min_data | - |
| 44 | Give me a match-type fix list: keyword, problem, fix and expected savi... | yes | search_query | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 45 | Which match type works best in each bucket (attack, defend, expand)? | yes | search_query | defend_terms, attack_terms (optional) | - |
| 46 | What is my average cost per 1,000 impressions by ad placement? | yes | granular, search_query | - | - |
| 47 | Which search queries have the highest CPM, and are they converting? | yes | granular, search_query | - | - |
| 48 | Which campaigns have high CPM and low click-through? | yes | granular, search_query | - | - |
| 49 | How do CPM-bid and CPC-bid campaigns compare on cost per conversion? | yes | granular, search_query | - | - |
| 50 | Which CPC campaigns are paying more per click than I accept? | partial | granular | cpc_acceptable_by_bucket | Shows current CPC by bucket; the 'above what I accept' part says needs setting |
| 51 | Is Collection Ads IM at ₹9,000 a day still worth it, given its GMV tre... | partial | granular | contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 52 | Which keywords pay a high CPM for a poor ad rank? | yes | granular, search_query | - | - |
| 53 | Where am I paying for top ad rank without a matching return? | partial | granular | contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 54 | Which campaigns don't spend their full daily budget, and why might tha... | yes | granular, search_query | - | - |
| 55 | Has CPM gone up this week in any placement or city? | yes | granular, search_query | - | - |
| 56 | Which placements give the cheapest cost per order? (Cost per order use... | yes | granular, search_query | - | - |
| 57 | Where should I lower bids, and by how much? | partial | granular | cpm_acceptable_by_placement, cpc_acceptable_by_bucket | Shows current CPM/CPC by placement or bucket; the 'by how much' part says needs setting |
| 58 | Rank cities by spend and ROAS. Which ones are below break-even? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 59 | Which cities have high spend and weak ROAS? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 60 | Which cities have strong ROAS but get too little spend? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 61 | Are Bangalore and Mumbai getting the right share of the budget? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 62 | For a given city, which keywords are wasting the most? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 63 | Which cities have high CPM compared with the account average? | yes | granular | min_data, contribution_before_ads_pct_of_mrp_gmv | - |
| 64 | Which cities got worse this week, and is it cost, conversion or availa... | partial | granular; availability not loaded | lag_days | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 65 | Merge "Noida" and "Noida 1" and show me the combined view. | yes | granular | - | - |
| 66 | Group cities by tier and compare ROAS by tier. (City tiers come from s... | partial | granular | city_tiers | Shows ROAS by city; tier grouping says needs setting: city_tiers |
| 67 | Which cities should I cut, hold or scale this week? | partial | granular; stock not loaded | min_data, contribution_before_ads_pct_of_mrp_gmv, scale_rule | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 68 | Rank SKUs by spend and ROAS. | partial | granular | product_title_to_sku, min_data, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 69 | Which SKUs get heavy spend at weak ROAS? | partial | granular | product_title_to_sku, min_data, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 70 | Which SKUs have strong ROAS and room to scale? | partial | granular; stock not loaded | product_title_to_sku, scale_rule, hero_skus | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 71 | Which SKUs are advertised on searches that don't match them? (A search... | partial | search_query | sku_keywords, product_title_to_sku | Asks which SKU keywords to use, shows spend by SKU x query meanwhile |
| 72 | For a given SKU, which searches and cities drive its results? | yes | granular | - | - |
| 73 | Which SKUs have high click-through but low conversion (possible price,... | yes | granular | product_title_to_sku (display) | - |
| 74 | Are any SKUs taking sales from each other? | partial | granular; hypothesis only | - | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 75 | Which SKUs should get more spend, and is there enough stock to support... | partial | granular; stock not loaded | product_title_to_sku, scale_rule, hero_skus | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 76 | Which SKU and city combinations are the most wasteful? | partial | granular | product_title_to_sku, min_data, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 77 | Give me a SKU × city × match type grid for my top 5 SKUs. | partial | granular | product_title_to_sku, min_data, contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 78 | How is spend split across attack, defend and expand this week, versus... | yes | search_query, granular | split_target (for gap); defend_terms, attack_terms (optional) | - |
| 79 | What is ROAS for each bucket, judged by the searches actually served o... | yes | search_query, granular | split_target (for gap); defend_terms, attack_terms (optional) | - |
| 80 | How much of my defend spend actually landed on brand searches? | yes | search_query, granular | split_target (for gap); defend_terms, attack_terms (optional) | - |
| 81 | Which competitor keywords are worth keeping, and which should I cut? | partial | search_query, granular | competitor_closeness | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 82 | How are Too Yum, Troovy, Let's Try and Choki Choki doing as attack key... | yes | search_query, granular | min_roas (optional) | - |
| 83 | Which competitors seem closest to my products, judging by conversion? | partial | search_query, granular | competitor_closeness | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 84 | Is my brand defence holding its ad rank on brand searches? | yes | search_query, granular | min_roas (optional) | - |
| 85 | Is Guaranteed Top Slot on brand searches still efficient? | yes | search_query, granular | min_roas (optional) | - |
| 86 | Which expand keywords are my growth engines, and which are burning mon... | yes | search_query, granular | min_roas (optional) | - |
| 87 | Which category keywords should I add, based on searches that already c... | yes | search_query | - | - |
| 88 | How much attack spend leaked into non-competitor searches? | yes | search_query, granular | min_roas (optional) | - |
| 89 | Give me a scorecard for the three buckets: spend, share, ROAS, trend a... | yes | search_query, granular | min_roas (optional) | - |
| 90 | Which campaigns kept spending while availability was poor? | no | stock / fill rate: data not loaded | - | Returns 'data not loaded: ...' then offers the nearest answerable view |
| 91 | Which SKU and city combinations should I pause until fill rate recover... | no | stock / fill rate: data not loaded | - | Returns 'data not loaded: ...' then offers the nearest answerable view |
| 92 | How much of this week's ROAS drop is likely availability rather than a... | no | stock / fill rate: data not loaded | - | Returns 'data not loaded: ...' then offers the nearest answerable view |
| 93 | Which placements keep serving even when products are out of stock? | no | stock / fill rate: data not loaded | - | Returns 'data not loaded: ...' then offers the nearest answerable view |
| 94 | If I combine this report with my fill rate data, where am I wasting th... | no | stock / fill rate: data not loaded | - | Returns 'data not loaded: ...' then offers the nearest answerable view |
| 95 | I need to cut ₹50,000 this week. Where should it come from? | partial | granular, search_query; stock not loaded | waste_rule, scale_rule, min_data, budget_increase_step_pct | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 96 | I have an extra ₹50,000. Where should it go? | partial | granular, search_query; stock not loaded | waste_rule, scale_rule, min_data, budget_increase_step_pct | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 97 | What did the extra spend since I last raised budgets actually bring ba... | no | budget change log: data not loaded | - | Returns data not loaded: budget change log, then offers a daily spend vs GMV trend (no causal claim) |
| 98 | At what spend level does each top keyword stop paying back? | partial | granular | contribution_before_ads_pct_of_mrp_gmv | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 99 | How should I phase the rest of this month's budget to hit target witho... | partial | granular | monthly_budget_inr, monthly_gmv_target_inr, a2s_ceiling_pct | Shows spend to date and daily run rate; the phasing says needs setting |
| 100 | Which campaigns should I pause, reduce, hold or scale? Give me a table... | partial | granular, search_query; stock not loaded | waste_rule, scale_rule, min_data, budget_increase_step_pct | Full numbers; every scale or spend advice adds 'depends on stock, not in data' |
| 101 | What are the risks if I follow your recommendations? | partial | follows from the answers above | - | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 102 | How will I know next week whether the changes worked? | partial | follows from the answers above | - | Full numbers with the rule parts unjudged: 'needs setting: <key>' or 'too little data' |
| 103 | Why did ROAS fall this week? Show me the chain of causes. | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 104 | Is this a one-off dip or a lasting structural problem? | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 105 | What looks good in this report but is actually misleading? | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 106 | Which recommendation are you least sure about, and why? | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 107 | What data would change your recommendation? | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 108 | Challenge my decision to raise spend for a higher target. | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 109 | Write a 5-line update for my founder on this week's Instamart ads. | partial | 5-line template filled with computed numbers (draft for review) | - | Supporting numbers always; judgement note attached |
| 110 | What principle should I take away from this week's data? | partial | supporting numbers only; judgement needs human review | lag_days, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |
| 111 | Let's brainstorm: how do I grow Instamart ads revenue 30% next month w... | partial | supporting numbers + options template | monthly_gmv_target_inr, contribution_before_ads_pct_of_mrp_gmv | Supporting numbers always; judgement note attached |

Totals: yes 61, partial 44, no 6.

## Known data gaps (return `data not loaded: <item>`)
- Stock, fill rate, availability (Q64 part, Q75 part, Q90 to Q94 and every scale recommendation)
- New-to-brand data (attack judgement uses conversion only)
- Budget or cost change log (Q97)
- SKU mapping for Collection Ads, Auto Suggest, Search Inline Banner (Q17 can still show the % without SKU; collection_ads_sku_map for more)
- Search query x city (never available; offer keyword x city and search query x keyword)

## Beyond the 111: free-form data questions

The 111 are the tested core, not a limit. Anything that reads or compares the loaded data goes through a second path, the **flexible query engine** (`bot/flex.py`). It builds parameterised SQL from whitelisted metric and dimension names only (never from user text), so it can answer questions nobody wrote down.

| Family | Example | How |
|---|---|---|
| Lookup / filter | "Show rows for Troovy campaign in Pune on 3 Sept", "ad rank for 'ragi chips' by city" | Filters on campaign, city, SKU, keyword, query, placement, match type, date; row-level allowed, capped at 50 rows with a "showing 50 of N" line |
| Top / bottom N | "Top 15 queries by clicks", "10 lowest CTR keywords with 5k+ impressions" | Any metric x any dimension, optional min_data and threshold filters |
| Trend | "Daily spend and ROAS last 14 days", "weekly GMV by city", "is CPM rising?" | Day or Monday-week buckets, text table, direction + slope + first-to-last change, lag days marked |
| Period compare | "This week vs last week by placement", "August vs July" | Any two periods (relative, named month, or explicit dates, Asia/Kolkata), deltas recomputed from totals, growth from zero base = n/a |
| Share / mix | "What share of GMV is Collection Ads?" | Share of total for any metric |
| Ratios on demand | "CPC by city", "conversion rate by SKU", "cost per order by placement" | Ratios always recomputed from summed numerators and denominators, never averaged |
| Existence / counts | "Is 'chocolate' a search query we served on?", "how many cities did Troovy run in?", "which months are loaded?" | Distinct counts and lookups; fuzzy name match with 'did you mean' |
| Anomaly / outlier | "Any day with unusual spend?", "which city's CPM jumped?" | Compare to the metric's own median and spread in the loaded data; no external benchmark |
| Composite | "Cities where ROAS fell but spend rose" | Two-period compare plus a filter on both deltas |
| Cross-checks | "Do the three tables agree?" | Totals for search placements across granular, search_query and summary; warns on mismatch |

Limits, always stated in the answer: search query x city is not available; stock is not in the data; averages are recomputed from totals; a comparison between placements for CPM is refused (same placement only). If a question maps to nothing, it goes to the miss log and the bot says what it could answer instead.
