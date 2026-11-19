---
trigger: always_on
---

# Measure Before Assuming a Bottleneck

When diagnosing a timeout, hang, or performance regression:
1. **Time each stage** of the actual failing path (subprocesses, I/O, DB calls, and returned payload sizes) before proposing a fix.
2. **Never blame a function from code inspection alone**—verify with a quick timing print or stage-by-stage check first.
