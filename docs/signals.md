# Source signal semantics

Detector events сохраняются независимо от пользовательских Telegram rules. Pine alert toggles продолжают существовать, `alert_conditions` содержит точные разрешённые source alertconditions; в UI rules можно выбирать detector events независимо.

| Event | Pine variable | Source line | Timing / source formula |
|---|---|---|---|
| L WATCH | `newLongWatch` | 4437 | `alertBarOk and directionLong and fsmNewWatchEvent` |
| S WATCH | `newShortWatch` | 4438 | `alertBarOk and directionShort and fsmNewWatchEvent` |
| LONG WATCH ENTRY | `watchEntryLongSignal` | 4450 | `newLongWatch and watchEntryFiltersAllowed` |
| SHORT WATCH ENTRY | `watchEntryShortSignal` | 4451 | `newShortWatch and watchEntryFiltersAllowed` |
| MATURED PRE-BREAK ENTRY | `maturedEntryLongSignal` | 4463 | `alertBarOk and directionLong and not newLongWatch and setupFsmState >= FSM_WATCH and maturedEntryFiltersAllowed and nz(maturedEntryLastGeneration, -1) != nz(lockedStartBar, -1)` |
| LONG ARMED | `newLongArmed` | 4473 | `alertBarOk and directionLong and fsmNewArmedEvent and milestoneExecutionAllowed` |
| SHORT ARMED | `newShortArmed` | 4474 | `alertBarOk and directionShort and fsmNewArmedEvent and milestoneExecutionAllowed` |
| PINE READY LONG | `newLongEntry` | 4478 | `alertBarOk and directionLong and fsmNewReadyEvent` |
| PINE READY SHORT | `newShortEntry` | 4479 | `alertBarOk and directionShort and fsmNewReadyEvent` |
| BREAKOUT | `newLongBreakout` | 4486 | `alertBarOk and directionLong and breakoutLong` |
| BREAKDOWN | `newShortBreakdown` | 4487 | `alertBarOk and directionShort and breakdownShort` |
| BRONZE | `setupQualityBronzeSignal` | 4411 | `setupQualityBronzeReached` |
| STRONG | `setupQualityStrongSignal` | 4412 | `setupQualityStrongReached` |
| LONG REVERSAL RISK | `newLongReversalRisk` | 4434 | `(reversalWarningReached and directionLong) or (alertBarOk and directionLong and lifecycleFailedSweepAgainstDirection)` |
| SHORT REVERSAL RISK | `newShortReversalRisk` | 4435 | `(reversalWarningReached and directionShort) or (alertBarOk and directionShort and lifecycleFailedSweepAgainstDirection)` |
| ACTIVE PLAN DEGRADED | `activePlanDegradedSignal` | 4493 | `barstate.isconfirmed and activePlanNow and activePlanNoAdd and not activePlanExit and (not sameSetupGeneration or not activePlanNoAdd[1])` |
| ACTIVE PLAN EXIT | `activePlanExitSignal` | 4492 | `barstate.isconfirmed and activePlanExit and (not sameSetupGeneration or not activePlanExit[1])` |
| ADD-ON | `addOnAllowedSignal` | 4494 | `barstate.isconfirmed and addOnAllowed and (not sameSetupGeneration or not addOnAllowed[1])` |
| LONG TP HIT | `longT1Hit` | 4584 | `alertBarOk and longTargetActive and not na(trackedLongT1) and not na(trackedLongEntryBar) and bar_index > trackedLongEntryBar and high >= trackedLongT1` |
| SHORT TP HIT | `shortT1Hit` | 4585 | `alertBarOk and shortTargetActive and not na(trackedShortT1) and not na(trackedShortEntryBar) and bar_index > trackedShortEntryBar and low <= trackedShortT1` |
| LONG ARMED LOST | `longArmedLost` | 4488 | `alertBarOk and sameSetupGeneration and directionLong and fsmArmedDegraded and not fsmArmedDegraded[1] and not newLongEntry` |
| SHORT ARMED LOST | `shortArmedLost` | 4489 | `alertBarOk and sameSetupGeneration and directionShort and fsmArmedDegraded and not fsmArmedDegraded[1] and not newShortEntry` |
| LONG BOUNCE WATCH | `newBounceLong` | 4490 | `alertBarOk and bounceLong and not bounceLong[1]` |
| SHORT BOUNCE WATCH | `newBounceShort` | 4491 | `alertBarOk and bounceShort and not bounceShort[1]` |
| AVG SETUP >= 70 | `avgSetup70Signal` | 4107 | `avgSetup70CrossThisBar` |
| EXECUTION QUALITY >= 65 | `execution65Signal` | 4128 | `execution65CrossThisBar` |

WATCH/ARMED/READY следуют confirmed FSM; BRONZE/STRONG жёстко confirmed. AVG70/Execution65 — latched intrabar events. Break/Target/Bounce/Reversal зависят от `alertsOnConfirmedClose`. MATURED следует **именно** source `alertBarOk`, хотя часть комментариев Pine описывает его как confirmed: при выключении флага исходная формула может разрешить intrabar событие, и baseline это не исправляет.

Frozen plan устанавливается при исходном READY event; T1 hit исключает signal candle. Active health имеет приоритет в ACTION, не откатывая persistent FSM назад. Все действия и path codes: `reference/enums.json`.
