# Eval results

Run against `https://perfect-saturday-planner-nine.vercel.app` on 2026-11-24 18:55 UTC with `python3 evals/run_evals.py --repeat 2` (10 scenarios × 2 runs each).

**8/10 scenarios passed every check in every run (18/20 runs clean).** Plans took 0.1–16.5 s (median 7.2 s); agent plans used 2.6 LLM calls on average.

| Scenario | Result | Checks | Stops (first passing run) | Time | LLM calls | Tokens |
|---|---|---|---|---|---|---|
| The assignment's example input | ⚠️ 1/2 runs | 11/11 | Venkatappa Art Gallery → Chowdiah Memorial Hall → Konark Kanteerava | 6.9 s | 3 | 15456 |
| Vague chat request | ✅ 2/2 runs | 2/2 | asked: city, budget, time, mood | 2.0 s | 0 | 0 |
| Vague request + answers (Pune) | ✅ 2/2 runs | 10/10 | 7 to 9 Grams Coffee → Osho Teerth Park → Blue Tokai Coffee Roasters | 7.5 s | 2 | 7141 |
| Rain all afternoon (simulated) | ✅ 2/2 runs | 8/8 | Ainmane → Manora → Artisera | 8.2 s | 2 | 6719 |
| Smaller city with thin map data (Nashik) | ✅ 2/2 runs | 8/8 | Baba Ambedkar Municipal Gardens → Kulkarni's Pav Bhaji → WeGo Art Gallery and Art Exhibition | 9.7 s | 3 | 12531 |
| Tight budget (₹300 in Mumbai) | ⚠️ 1/2 runs | 8/8 | Colaba Woods Garden → Mumbai Port Trust Garden → art gallery → Oxford Bookstore | 14.7 s | 4 | 23797 |
| Wish only available far away (bowling, Gurugram) | ✅ 2/2 runs | 9/9 | The Big Shot → Om Bookshop → Barista | 8.0 s | 2.5 | 12496 |
| History, art and a North Indian lunch (Delhi) | ✅ 2/2 runs | 9/9 | Lokayata Art Gallery → Maheshi Da Frontier Punjabi Dhaba → OPS → Barah Khamba → Kunzum | 8.2 s | 2 | 7742 |
| Outside India (London) | ✅ 2/2 runs | 9/9 | Nero Express → National Gallery → Notes | 4.8 s | 2 | 7650 |
| Every LLM unavailable (simulated) | ✅ 2/2 runs | 9/9 | St. John's Auditorium → Utsav | 1.0 s | 0 | 0 |

## Failed checks

- **spec-example** · passes validation (1 of 2 runs): 1 validation error(s) left in the final plan: Venkatappa Art Gallery is open 10:00–17:00 on Saturday, but this visit runs 16:40–17:10 (after travel). Move i
- **tight-budget** · passes validation (1 of 2 runs): 1 validation error(s) left in the final plan: Estimated total 370 INR is well over the 300 budget: places 180, travel 172 (including the ride home), buffer 

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
