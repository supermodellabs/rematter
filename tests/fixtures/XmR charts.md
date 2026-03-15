---
created: 2026-01-21 13:58
modified: 2026-02-13 14:20
synced:
publish: false
---
[Becoming Data Driven - Common Cog](https://commoncog.com/becoming-data-driven-first-principles/)
This is a straight up classic foundational text.

---
> [!note] 🥰
> The below is from discussion of the above article with Claude Opus 4.5 with Reasoning fairly high

## What is Sigma (σ)?

**Sigma (σ)** refers to the **standard deviation** — a measure of how spread out data points are from the average (mean). In a distribution:

- **1σ** covers ~68% of data points
- **2σ** covers ~95% of data points
- **3σ** covers ~99.7% of data points

## How It Works in XmR Charts

An XmR (Individuals and Moving Range) chart sets **control limits** at ±3 sigma from the center line (the process average):

```plain text
Upper Control Limit (UCL) ─────────────  +3σ


Center Line ───────────────────────────  Mean


Lower Control Limit (LCL) ─────────────  -3σ

```

## Why 3 Sigma?

Shewhart's choice of 3σ is a **pragmatic trade-off**:

| Consideration | Explanation |
| --- | --- |
| **Sensitivity** | Wide enough to avoid chasing random noise (false alarms) |
| **Detection** | Narrow enough to catch real signals (true problems) |
| **False positive rate** | ~0.3% for normal distributions; ~3% for real-world distributions |

If a point falls **outside** the 3σ limits, there's only a ~0.3% chance (for normal data) it happened by random variation alone — suggesting something meaningful changed.

## The Key Insight

The passage emphasizes that XmR charts aren't trying to prove statistical significance — they're trying to detect when **a different probability distribution** has entered your process. A point outside 3σ signals: *"This likely came from a different system than your baseline."*
This makes it practical for business decisions where you need to act quickly on real changes without being paralyzed by noise.

## *How* You Calculate σ is crucial

The standard deviation formula looks at **all your data points** and calculates spread:
$\sigma = \sqrt{\frac{\sum(x_i - \bar{x})^2}{n-1}}$
The problem: this formula **assumes all data comes from one distribution**. If your dataset already contains points from a *different* distribution (the very thing you're trying to detect), you'll accidentally include those outliers in your σ calculation, which will:

- **Inflate your estimated σ**
- **Make your control limits too wide**
- **Hide the signals you're looking for**

## The Moving Range Method

Instead, XmR charts estimate σ using the **average moving range** — the average difference between consecutive points:
$\bar{R} = \frac{\sum |x_i - x_{i-1}|}{n-1}$
Then: $\hat{\sigma} = \bar{R} / 1.128$
This is more **robust** because consecutive points are likely from the same distribution, even if your overall dataset contains signals from different sources.

### A Tale of Two Methods

Let's use concrete data. Imagine you're tracking daily sales, and your process is stable around **10 units** — except one day something weird happened and you sold **25**.
**Your data:** `10, 11, 9, 10, 11, 25, 10, 9, 11, 10`

---

### Standard Deviation Approach

**Step 1: Calculate the mean**
$\bar{x} = \frac{10+11+9+10+11+25+10+9+11+10}{10} = 11.6$
Notice the outlier (25) **pulled your mean up** from ~10 to 11.6.
**Step 2: Calculate squared deviations from that mean**

| Point | Deviation from 11.6 | Squared |
| --- | --- | --- |
| 10 | -1.6 | 2.56 |
| 11 | -0.6 | 0.36 |
| 9 | -2.6 | 6.76 |
| 10 | -1.6 | 2.56 |
| 11 | -0.6 | 0.36 |
| **25** | **+13.4** | **179.56** |
| 10 | -1.6 | 2.56 |
| 9 | -2.6 | 6.76 |
| 11 | -0.6 | 0.36 |
| 10 | -1.6 | 2.56 |

**Sum = 204.4**
$\sigma = \sqrt{\frac{204.4}{9}} \approx 4.77$
**The problem:** That one outlier contributed **179.56** out of 204.4 — it's **88%** of your entire variance estimate! Your σ is massively inflated.
Your 3σ limits would be: 11.6 ± 14.3 → **[-2.7, 25.9]**
The outlier *hides itself* by widening the limits enough to include itself. 🙈

---

### Moving Range Approach

#### Step 1: Calculate differences between consecutive points

| Points | Difference |
| --- | --- |
| 10 → 11 | 1 |
| 11 → 9 | 2 |
| 9 → 10 | 1 |
| 10 → 11 | 1 |
| 11 → 25 | **14** |
| 25 → 10 | **15** |
| 10 → 9 | 1 |
| 9 → 11 | 2 |
| 11 → 10 | 1 |

#### Step 2: Average the ranges

$\bar{R} = \frac{1+2+1+1+14+15+1+2+1}{9} = 4.22$

#### Step 3: Estimate sigma

$\hat{\sigma} = \frac{\bar{R}}{1.128} \approx 3.74$
Still somewhat inflated, but notice: the outlier only "contaminated" **2 out of 9 ranges** (the jump up *to* 25, and the drop *from* 25).

---

## The Key Intuition

| Standard Deviation | Moving Range |
| --- | --- |
| Compares every point to **one global reference** (the mean) | Compares each point to **its neighbor** |
| Outlier shifts the mean AND adds huge squared deviation | Outlier only affects 2 adjacent ranges |
| One bad point corrupts the whole calculation | Damage is localized |

**Why neighbors?** If your process suddenly shifts (new supplier, broken machine, holiday surge), consecutive points will *both* be in the new regime. The variation *between* them still reflects normal noise. The signal shows up as a *level change*, not inflated within-range variation.

---

## Visual Intuition

```plain text
Stable:     10 ─ 11 ─ 9 ─ 10 ─ 11 ─ 10 ─ 9 ─ 11 ─ 10
               ↕    ↕   ↕    ↕    ↕   ↕   ↕    ↕
             small hops = true process noise

With signal: 10 ─ 11 ─ 9 ─ 10 ─ 11 ─ 25 ─ 10 ─ 9 ─ 11
                                   ↕    ↕
                              BIG  BIG  (only 2 affected)

```

The moving range isolates the signal's impact to where it actually occurred — the *transition points* — rather than letting it pollute your entire noise estimate.
**Step 2: Average the ranges**
$\bar{R} = \frac{1+2+1+1+14+15+1+2+1}{9} = 4.22$
**Step 3: Estimate sigma**
$\hat{\sigma} = \frac{\bar{R}}{1.128} \approx 3.74$

---
The 1.128 is a statistical constant (called d₂) that converts the average moving range into an unbiased estimate of sigma for individual measurements. It comes from the mathematical relationship between the range and standard deviation of a normal distribution when you're comparing pairs of consecutive points.
