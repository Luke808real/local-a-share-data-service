# ASL Daily Facts 2026-09-09 Blocker Diagnosis V01

This is an offline evidence audit. It made zero provider calls and did not modify either publication authority.

## Frozen set relation

- Preclose keys: 16
- Trade-status keys: 10
- Intersection: 0
- Preclose only: 16
- Trade-status only: 10
- Null numeric keys: 10; intersection with status conflicts: 10

## Preclose mismatch keys

| Symbol | Name | BaoStock preclose | R3 2026-09-08 close | Difference | Classification | Local evidence |
|---|---|---:|---:|---:|---|---|
| 001400.SZ | 江顺科技 | 79.6700 | 80.1700 | -0.5000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002073.SZ | 软控股份 | 5.8600 | 5.8800 | -0.0200 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002315.SZ | 焦点科技 | 25.4800 | 25.9800 | -0.5000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002322.SZ | 理工能科 | 12.3800 | 12.7100 | -0.3300 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002441.SZ | 众业达 | 8.1800 | 8.3800 | -0.2000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002833.SZ | 弘亚数控 | 18.6100 | 18.9100 | -0.3000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 002841.SZ | 视源股份 | 46.9000 | 47.4000 | -0.5000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 300196.SZ | 长海股份 | 16.7000 | 16.9000 | -0.2000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 300622.SZ | 博士眼镜 | 15.4600 | 15.6200 | -0.1600 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 301151.SZ | 冠龙节能 | 22.3800 | 22.5800 | -0.2000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 600114.SH | 东睦股份 | 28.7300 | 28.8300 | -0.1000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 603992.SH | 松霖科技 | 20.0200 | 20.3000 | -0.2800 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 603993.SH | 洛阳钼业 | 18.8500 | 18.9400 | -0.0900 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 605377.SH | 华旺科技 | 8.0200 | 8.2200 | -0.2000 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 688128.SH | 中国电研 | 21.8900 | 22.1400 | -0.2500 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |
| 688271.SH | 联影医疗 | 106.8700 | 107.0000 | -0.1300 | UNRESOLVED | YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE |

## Frozen trade-status exception keys

| Symbol | Name | BaoStock raw | Normalized | R3 volume | R3 amount | Classification | Null numeric fields |
|---|---|---|---|---:|---:|---|---|
| 000016.SZ | *ST康佳A | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 002731.SZ | *ST萃华 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 002870.SZ | 香山股份 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 002998.SZ | 优彩资源 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 301139.SZ | *ST元道 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 600825.SH | 新华传媒 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 600929.SH | 雪天盐业 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 605577.SH | 龙版传媒 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 688291.SH | 金橙子 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |
| 688432.SH | 有研硅 | 0 | SUSPENDED | 0 | 0.00 | PASS_SUSPENDED_ZERO_BAR | pct_chg, turnover_rate |

## Result

All 16 preclose rows remain `UNRESOLVED`: any related local corporate-action record carries only `ex_date=2026`, not an exact event date or reference price. It is not eligible to turn a mismatch into a pass.

All 10 status rows retain their provider evidence (`tradestatus=0`, empty `pctChg` and `turn`) and have a matching R3 zero-volume/zero-amount carry-forward bar (`open=high=low=close=preclose`). They are aligned suspended sessions, not provider-status conflicts and not an adapter bug. The 10 numeric-null keys exactly equal these 10 suspension-aligned keys.

Certification remains blocked. No normal row, tri-state rule, R3 data, RAW evidence, or publication pointer was changed.
