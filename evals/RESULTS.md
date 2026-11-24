# Eval results

Run against `https://perfect-saturday-planner-nine.vercel.app` on 2026-11-24 20:37 UTC with `python3 evals/run_evals.py --repeat 2` (10 scenarios × 2 runs each).

**7/10 scenarios passed every check in every run (17/20 runs clean).** Plans took 0.1–21.8 s (median 7.7 s); agent plans used 2.4 LLM calls on average.

| Scenario | Result | Checks | Stops (first passing run) | Time | LLM calls | Tokens |
|---|---|---|---|---|---|---|
| The assignment's example input | ⚠️ 1/2 runs | 11/11 | Cubbon Park → Konark Kanteerava → Chowdiah Memorial Hall | 5.1 s | 2 | 7861 |
| Vague chat request | ✅ 2/2 runs | 2/2 | asked: city, budget, time, mood | 2.0 s | 0 | 0 |
| Vague request + answers (Pune) | ✅ 2/2 runs | 10/10 | 7 to 9 Grams Coffee → Osho Teerth Park → Shahu Modak Udyan | 4.7 s | 2 | 7330 |
| Rain all afternoon (simulated) | ✅ 2/2 runs | 8/8 | Ainmane → Manora → Artisera → Champaca | 7.4 s | 2.5 | 10883 |
| Smaller city with thin map data (Nashik) | ✅ 2/2 runs | 8/8 | Baba Ambedkar Municipal Gardens → Kulkarni's Pav Bhaji → WeGo Art Gallery and Art Exhibition | 9.8 s | 2 | 6436 |
| Tight budget (₹300 in Mumbai) | ⚠️ 1/2 runs | 8/8 | Colaba Woods Garden → art gallery → Kitab Khana | 14.9 s | 3.5 | 19526 |
| Wish only available far away (bowling, Gurugram) | ⚠️ 1/2 runs | 9/9 | The Big Shot → The Laugh Store | 12.2 s | 3.5 | 21314 |
| History, art and a North Indian lunch (Delhi) | ✅ 2/2 runs | 9/9 | Barah Khamba → Lokayata Art Gallery → Maheshi Da Frontier Punjabi Dhaba → OPS → Kunzum | 11.7 s | 2 | 7498 |
| Outside India (London) | ✅ 2/2 runs | 9/9 | National Gallery → Notes → National Portrait Gallery | 7.1 s | 2 | 6974 |
| Every LLM unavailable (simulated) | ✅ 2/2 runs | 9/9 | St. John's Auditorium → Utsav | 1.0 s | 0 | 0 |

## Failed checks

- **spec-example** · quiet or explained (1 of 2 runs): likely-crowded stop with no trade-off: Cubbon Park
- **tight-budget** · passes validation (1 of 2 runs): 1 validation error(s) left in the final plan: Estimated total 369 INR is well over the 300 budget: places 150, travel 201 (including the ride home), buffer 
- **far-wish** · bowling: served or explained (1 of 2 runs): no bowling stop and no trade-off explaining why

## What each scenario checks

- **spec-example**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, vegetarian food, has food stop, quiet or explained, music served or explained
- **vague-chat**: asks for city, does not plan yet
- **chat-answers**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, start time 14:00, has café stop, walk: served or explained
- **rain**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, indoor in rain
- **thin-data**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, has food stop
- **tight-budget**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, free stop
- **far-wish**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, bowling: served or explained, has bar stop
- **cuisine-history**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, has historic stop, north indian food
- **international**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, local currency, has art stop
- **llm-down**: passes validation, stop count, every stop explained, no repeated place, within budget, inside time window, has route map, rule based plan, vegetarian food
