# Enhanced NLU Evaluation Report

- Generated: 2026-10-28T11:23:41+08:00
- Model: `gpt-6-luna`
- Provider: `openai`
- Evaluation mode: `live`
- Cases: 54
- Exact matches: 53/54 (98.1%)

## Component Accuracy

| Component | Accuracy |
| --- | ---: |
| intent_sequence | 100.0% |
| language | 100.0% |
| slots | 98.1% |
| missing_slots | 98.1% |
| issue_types | 98.1% |
| slot_updates | 100.0% |
| is_ambiguous | 100.0% |
| no_new_tasks_for_outcome | 100.0% |

## Task-Level Metrics

- Task intent accuracy: 61/61 (100.0%)
- Slot precision / recall / F1: 98.5% / 100.0% / 99.2%
- Missing-slot precision / recall / F1: 100.0% / 91.7% / 95.7%
- Slot-update exact cases: 2/2 (100.0%)
- Slot-update precision / recall / F1: 100.0% / 100.0% / 100.0%
- Ambiguity accuracy: 2/2 (100.0%)

## Error Categories

- issue_type_error: 1
- missing_slot_error: 1
- slot_extra: 1

## Accuracy by Category

| Category | Cases | Exact accuracy |
| --- | ---: | ---: |
| chinese | 6 | 100.0% |
| colloquial | 7 | 100.0% |
| dual_intent | 7 | 100.0% |
| english | 6 | 100.0% |
| missing_slots | 6 | 100.0% |
| mixed_language | 6 | 100.0% |
| slot_update | 1 | 100.0% |
| standard | 8 | 87.5% |
| too_many_tasks | 1 | 100.0% |
| unknown_intent | 6 | 100.0% |

## Failed Cases

### eval-050 — standard

Input: `Can we accept 0 TOPS pieces by 2026-04-25?`

Status: `failed`

Failed checks: slots, missing_slots, issue_types
Error categories: issue_type_error, missing_slot_error, slot_extra

```json
{
  "expected": {
    "language": "en",
    "intents": [
      "order_feasibility"
    ],
    "slots": [
      {
        "category": "TOPS",
        "requested_delivery_date": "2026-04-25"
      }
    ],
    "missing_slots": [
      [
        "pieces"
      ]
    ],
    "issue_types": [
      "invalid_format"
    ]
  },
  "actual": {
    "language": "en",
    "intents": [
      "order_feasibility"
    ],
    "slots": [
      {
        "category": "TOPS",
        "requested_delivery_date": "2026-04-25",
        "pieces": 2
      }
    ],
    "missing_slots": [
      []
    ],
    "issue_types": []
  }
}
```
